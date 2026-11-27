import 'dart:async';
import 'dart:convert';

import 'package:flet/flet.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart' show MethodChannel;
import 'package:flutter_inappwebview/flutter_inappwebview.dart';

import 'app_webview_settings.dart';
import 'mobile_session_queue.dart';

const _cookiePayloadLimit = 32 * 1024;
const _maxInjectedCookies = 64;
const appWebViewCookieWaitTimeout = Duration(seconds: 4);
const _cookieQueueTimeout = Duration(seconds: 5);
const _popupSinkLifetime = Duration(seconds: 4);
const _popupSinkResolveTimeout = Duration(milliseconds: 800);
const _defaultSurfaceBgColor = Color(0xFF0F172A);
const _backgroundSyncChannel = MethodChannel('com.uthelper/background_sync');

class MobileAuthApplyResult {
  const MobileAuthApplyResult({
    required this.appliedCookies,
    required this.installedScriptOrigin,
  });

  final int appliedCookies;
  final String? installedScriptOrigin;
}

class _ResolvedCookieWrite {
  const _ResolvedCookieWrite({
    required this.webTarget,
    required this.host,
    required this.cookie,
  });

  final WebUri webTarget;
  final String host;
  final ValidatedAppCookie cookie;
}

/// Atomically applies validated host-only cookies and origin-pinned `AT_DOCUMENT_START`
/// user scripts under a single [MobileSessionQueue] lease so session updates never
/// interleave with logout cleanup, quiz origin purges, or concurrent navigations.
Future<MobileAuthApplyResult> applyAppWebViewAuthPayload({
  required List<dynamic> rawCookies,
  required String? rawInitScript,
  required String? rawAllowedOrigin,
  required Uri? targetUri,
  required Uri? initialUri,
  required bool viewerLocked,
  required bool blockUthLms,
  required bool Function() isDisposed,
  required Future<void> disposeSignal,
  required Future<void> Function(WebUri url, ValidatedAppCookie cookie)
      setCookie,
  Future<void> Function(WebUri url, String name, String path, String? domain)?
      deleteCookie,
  Future<void> Function()? clearAuthUserScripts,
  Future<void> Function(UserScript script)? addAuthUserScript,
  MobileSessionQueue? queue,
  Duration queueTimeout = _cookieQueueTimeout,
}) async {
  if (isDisposed()) {
    return const MobileAuthApplyResult(
      appliedCookies: 0,
      installedScriptOrigin: null,
    );
  }

  final validatedScript = buildOriginPinnedAppUserScript(
    source: rawInitScript,
    allowedOrigin: rawAllowedOrigin,
    targetUrl: targetUri?.toString(),
    viewerLocked: viewerLocked,
    blockUthLms: blockUthLms,
  );

  final cookieWrites = <_ResolvedCookieWrite>[];
  final targetAllowedForCookies = !viewerLocked &&
      rawCookies.isNotEmpty &&
      rawCookies.length <= _maxInjectedCookies &&
      targetUri != null &&
      targetUri.scheme.toLowerCase() == 'https' &&
      targetUri.host.isNotEmpty &&
      !(blockUthLms && isUthLmsBrowserUrl(targetUri.toString()));

  if (targetAllowedForCookies) {
    final sameAsInitial = initialUri != null &&
        initialUri.scheme.toLowerCase() == 'https' &&
        initialUri.host.isNotEmpty &&
        targetUri.host.toLowerCase() == initialUri.host.toLowerCase() &&
        targetUri.port == initialUri.port;
    final crossHostTrustedUth = isTrustedUthAuthUri(targetUri);

    if (sameAsInitial || crossHostTrustedUth) {
      for (final raw in rawCookies) {
        var effectiveCookieUri = targetUri;
        if (crossHostTrustedUth && raw is Map && raw['domain'] is String) {
          var d = (raw['domain'] as String).trim().toLowerCase();
          if (d.endsWith('.local')) {
            d = d.substring(0, d.length - '.local'.length);
          }
          if (d.startsWith('.')) {
            d = d.substring(1);
          }
          if (d == 'courses.ut.edu.vn' || d == 'thnn.ut.edu.vn') {
            effectiveCookieUri = Uri.parse('https://$d/');
          }
        }
        final cookie = validateAppWebViewCookie(raw, effectiveCookieUri);
        if (cookie != null) {
          final host = effectiveCookieUri.host.toLowerCase();
          cookieWrites.add(
            _ResolvedCookieWrite(
              webTarget: WebUri('https://$host/'),
              host: host,
              cookie: cookie,
            ),
          );
        }
      }
    }
  }

  if (cookieWrites.isEmpty &&
      validatedScript == null &&
      clearAuthUserScripts == null) {
    return const MobileAuthApplyResult(
      appliedCookies: 0,
      installedScriptOrigin: null,
    );
  }

  final sessionQueue = queue ?? sharedMobileSessionQueue;
  final timeout = Completer<void>();
  final timer = Timer(queueTimeout, timeout.complete);
  MobileSessionLease? lease;
  var applied = 0;
  String? installedOrigin;
  try {
    lease = await sessionQueue.acquire(
      cancelled: Future.any<void>([disposeSignal, timeout.future]),
    );
    if (lease == null || isDisposed()) {
      return const MobileAuthApplyResult(
        appliedCookies: 0,
        installedScriptOrigin: null,
      );
    }

    if (clearAuthUserScripts != null) {
      try {
        await clearAuthUserScripts();
      } catch (_) {}
    }
    if (validatedScript != null &&
        addAuthUserScript != null &&
        !isDisposed()) {
      try {
        await addAuthUserScript(validatedScript);
        installedOrigin = validatedScript.allowedOriginRules.first;
      } catch (_) {}
    }

    for (final entry in cookieWrites) {
      if (isDisposed()) break;
      if (deleteCookie != null &&
          entry.cookie.name.toLowerCase().startsWith('moodlesession')) {
        try {
          await deleteCookie(
            entry.webTarget,
            entry.cookie.name,
            entry.cookie.path,
            null,
          );
        } catch (_) {}
        try {
          await deleteCookie(
            entry.webTarget,
            entry.cookie.name,
            entry.cookie.path,
            entry.host,
          );
        } catch (_) {}
      }
      try {
        await setCookie(entry.webTarget, entry.cookie);
        applied++;
      } catch (_) {
        // Continue setting remaining cookies cleanly.
      }
    }
    return MobileAuthApplyResult(
      appliedCookies: applied,
      installedScriptOrigin: installedOrigin,
    );
  } finally {
    timer.cancel();
    lease?.release();
  }
}

/// Serializes host-only cookie installation through [MobileSessionQueue] so
/// writes never interleave with logout cleanup or quiz origin purges, and pins
/// cookie targets strictly to [initialUri]'s exact HTTPS host/port or verified
/// canonical UTH LMS hosts.
Future<int> installAppWebViewCookies({
  required List<dynamic> rawCookies,
  required Uri? targetUri,
  required Uri? initialUri,
  required bool Function() isDisposed,
  required Future<void> disposeSignal,
  required Future<void> Function(WebUri url, ValidatedAppCookie cookie)
      setCookie,
  MobileSessionQueue? queue,
  Duration queueTimeout = _cookieQueueTimeout,
}) async {
  if (initialUri == null ||
      initialUri.scheme.toLowerCase() != 'https' ||
      initialUri.host.isEmpty) {
    return 0;
  }
  final result = await applyAppWebViewAuthPayload(
    rawCookies: rawCookies,
    rawInitScript: null,
    rawAllowedOrigin: null,
    targetUri: targetUri,
    initialUri: initialUri,
    viewerLocked: false,
    blockUthLms: false,
    isDisposed: isDisposed,
    disposeSignal: disposeSignal,
    setCookie: setCookie,
    queue: queue,
    queueTimeout: queueTimeout,
  );
  return result.appliedCookies;
}

class _PopupSinkEntry {
  const _PopupSinkEntry({
    required this.windowId,
    required this.key,
    required this.allowInPlaceRedirect,
  });

  final int windowId;
  final int key;
  final bool allowInPlaceRedirect;
}

/// Returns true when two [InAppWebViewController] handles wrap the same
/// underlying platform WebView controller.
///
/// `flutter_inappwebview_android` allocates separate Dart wrapper instances for
/// `onWebViewCreated` and `onRenderProcessGone` via `controllerFromPlatform`,
/// so outer wrapper `identical(a, b)` is always false on Android.
bool isSameInAppWebViewController(
  InAppWebViewController? first,
  InAppWebViewController? second,
) {
  if (first == null || second == null) return false;
  return identical(first, second) || identical(first.platform, second.platform);
}

enum AppWebViewRenderProcessMethodOutcome {
  proceedLive,
  remountRecovery,
  failClosed,
}

/// Decides how `UthAppWebView._invokeMethod` handles a method call when the
/// Android WebView renderer has died, checking [renderProcessGone] BEFORE
/// inspecting [hasController] so stale controller references never block
/// recovery or invoke a dead WebView.
AppWebViewRenderProcessMethodOutcome resolveAppWebViewMethodDuringRenderProcessGone(
  String methodName, {
  required bool renderProcessGone,
  required bool hasController,
}) {
  if (renderProcessGone) {
    return switch (methodName) {
      'reload' ||
      'navigate' ||
      'load_html' ||
      'apply_auth_and_navigate' =>
        AppWebViewRenderProcessMethodOutcome.remountRecovery,
      'eval_js' || 'go_back' || 'go_forward' =>
        AppWebViewRenderProcessMethodOutcome.failClosed,
      _ => AppWebViewRenderProcessMethodOutcome.proceedLive,
    };
  }
  if (!hasController &&
      (methodName == 'eval_js' ||
          methodName == 'go_back' ||
          methodName == 'go_forward' ||
          methodName == 'reload')) {
    return AppWebViewRenderProcessMethodOutcome.failClosed;
  }
  return AppWebViewRenderProcessMethodOutcome.proceedLive;
}

/// Handles Android `WebViewClient.onRenderProcessGone` on the primary
/// `UthAppWebView` without terminating the host app process, ignoring stale
/// callbacks from previously unmounted WebView generations.
bool handleAppWebViewRenderProcessGone({
  required bool disposed,
  required bool mounted,
  required int callbackGeneration,
  required int activeGeneration,
  required RenderProcessGoneDetail detail,
  required VoidCallback clearDeadWebViewState,
  required void Function(String error) reportWebError,
}) {
  if (disposed || !mounted || callbackGeneration != activeGeneration) {
    return false;
  }
  clearDeadWebViewState();
  reportWebError(formatAppWebViewRenderProcessGoneError(detail.didCrash));
  return true;
}

/// Handles Android `WebViewClient.onRenderProcessGone` on offstage popup-cancelling
/// sink WebViews by safely tearing down the dead sink without crashing.
void handleAppWebViewPopupSinkRenderProcessGone({
  required bool disposed,
  required bool mounted,
  required VoidCallback removeSink,
}) {
  if (disposed || !mounted) return;
  removeSink();
}

class UthAppWebViewControl extends StatefulWidget {
  const UthAppWebViewControl({super.key, required this.control});

  final Control control;

  @override
  State<UthAppWebViewControl> createState() => _UthAppWebViewControlState();
}

class _UthAppWebViewControlState extends State<UthAppWebViewControl> {
  GlobalKey _webViewKey = GlobalKey();
  int _webViewGeneration = 0;
  InAppWebViewController? _controller;
  String _initialUrl = '';
  String _currentLoadedUrl = '';
  String? _initialHtml;
  String? _initialBaseUrl;
  String? _initialUserScript;
  String? _initialUserScriptAllowedOrigin;
  final AppWebViewAuthScriptTracker _authScriptTracker =
      AppWebViewAuthScriptTracker();
  UserScript? _deferredControllerUserScript;
  bool _viewerLocked = false;
  String? _viewerPrefix;
  Set<int> _allowedLoopbackPorts = const <int>{};
  bool _requireInitialCookies = false;
  bool _blockUthLms = false;
  Color _bgColor = _defaultSurfaceBgColor;
  String? _normalizedUserAgent;
  bool _prepared = false;
  bool _surfaceRevealed = false;
  bool _renderProcessGone = false;
  bool _hasCommittedPage = false;
  bool _hasReceivedDocumentTitle = false;
  bool _suppressCommitUntilNextLoadStart = false;
  bool _disposed = false;
  bool _cookieBootstrapStarted = false;
  bool _initialNavDispatched = false;
  bool _lateCookieReloaded = false;
  final Completer<void> _disposeSignal = Completer<void>();

  DataChannel? _cookieChannel;
  StreamSubscription<Uint8List>? _cookieSubscription;
  Uint8List? _pendingCookiePayload;
  Completer<Uint8List>? _cookiePayloadWaiter;
  Completer<void> _cookieBootstrapDone = Completer<void>();

  int _nextPopupSinkKey = 0;
  final List<_PopupSinkEntry> _popupSinks = [];
  final Map<int, Timer> _popupSinkTimers = {};
  final Set<int> _consumedPopupSinkKeys = <int>{};

  void _readControlProperties() {
    _initialUrl = widget.control.getString('url', '') ?? '';
    _initialHtml = widget.control.getString('html');
    _initialBaseUrl = widget.control.getString('base_url');
    _initialUserScript = widget.control.getString('initial_user_script');
    _initialUserScriptAllowedOrigin =
        widget.control.getString('initial_user_script_allowed_origin');
    _deferredControllerUserScript = null;
    _viewerLocked = widget.control.getBool('viewer_locked', false) ?? false;
    _requireInitialCookies =
        widget.control.getBool('require_initial_cookies', false) ?? false;
    _blockUthLms = widget.control.getBool('block_uth_lms', false) ?? false;
    _bgColor = parseAppWebViewColor(widget.control.getString('bgcolor')) ??
        _defaultSurfaceBgColor;

    final ports = <int>{};
    final rawPorts = widget.control.get('allowed_loopback_ports');
    if (rawPorts is List) {
      for (final item in rawPorts) {
        if (item is num) {
          final p = item.toInt();
          if (p >= 1 && p <= 65535) ports.add(p);
        }
      }
    }
    final initialUri = Uri.tryParse(_initialUrl);
    if (initialUri != null &&
        initialUri.scheme.toLowerCase() == 'http' &&
        (initialUri.host == '127.0.0.1' ||
            initialUri.host.toLowerCase() == 'localhost') &&
        initialUri.hasPort) {
      ports.add(initialUri.port);
    }
    _allowedLoopbackPorts = ports;
    _viewerPrefix = extractViewerLockPrefix(_initialUrl);
    if (_viewerPrefix != null || _initialHtml != null) {
      _viewerLocked = true;
    }
    final initialValidatedScript = buildOriginPinnedAppUserScript(
      source: _initialUserScript,
      allowedOrigin: _initialUserScriptAllowedOrigin,
      targetUrl: _initialUrl,
      viewerLocked: _viewerLocked,
      blockUthLms: _blockUthLms,
    );
    if (initialValidatedScript != null) {
      _authScriptTracker.arm(initialValidatedScript.allowedOriginRules.first);
    } else {
      _authScriptTracker.clear();
    }
  }

  void _openCookieChannelIfNeeded() {
    if (_viewerLocked || !_requireInitialCookies || _cookieChannel != null) return;
    final channel = FletBackend.of(context).openDataChannel();
    _cookieChannel = channel;
    _cookieSubscription = channel.messages.listen(_onCookiePayload);
    widget.control.triggerEvent('data_channel_open', {
      'channel_name': 'app_webview_cookies',
      'channel_id': channel.id,
    });
  }

  @override
  void initState() {
    super.initState();
    _readControlProperties();
    widget.control.addInvokeMethodListener(_invokeMethod);
    unawaited(_prepare());
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _openCookieChannelIfNeeded();
  }

  @override
  void didUpdateWidget(covariant UthAppWebViewControl oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (_disposed) return;
    if (oldWidget.control.id != widget.control.id) {
      oldWidget.control.removeInvokeMethodListener(_invokeMethod);
      widget.control.addInvokeMethodListener(_invokeMethod);
      _cookieSubscription?.cancel();
      _cookieSubscription = null;
      _cookieChannel?.close();
      _cookieChannel = null;
      _pendingCookiePayload?.fillRange(0, _pendingCookiePayload!.length, 0);
      _pendingCookiePayload = null;
      final waiter = _cookiePayloadWaiter;
      if (waiter != null && !waiter.isCompleted) {
        waiter.completeError(StateError('replaced'));
      }
      if (!_cookieBootstrapDone.isCompleted) {
        _cookieBootstrapDone.complete();
      }
      _cookieBootstrapDone = Completer<void>();
      _controller = null;
      _currentLoadedUrl = '';
      _surfaceRevealed = false;
      _renderProcessGone = false;
      _hasCommittedPage = false;
      _hasReceivedDocumentTitle = false;
      _suppressCommitUntilNextLoadStart = false;
      _cookieBootstrapStarted = false;
      _initialNavDispatched = false;
      _lateCookieReloaded = false;
      _webViewGeneration++;
      _webViewKey = GlobalKey();
      _readControlProperties();
      _openCookieChannelIfNeeded();
      return;
    }

    final nextBg = parseAppWebViewColor(widget.control.getString('bgcolor'));
    if (nextBg != null && nextBg != _bgColor) {
      setState(() => _bgColor = nextBg);
    }

    _requireInitialCookies =
        widget.control.getBool('require_initial_cookies', false) ?? false;
    _blockUthLms = widget.control.getBool('block_uth_lms', false) ?? false;
    final nextHtml = widget.control.getString('html');
    final nextBaseUrl = widget.control.getString('base_url');
    if (nextHtml != null && nextHtml != _initialHtml) {
      _initialHtml = nextHtml;
      _initialBaseUrl = nextBaseUrl;
      if (_renderProcessGone) {
        _remountAfterRenderProcessGone(html: nextHtml, baseUrl: nextBaseUrl);
        return;
      }
      final ctrl = _controller;
      if (ctrl != null) {
        unawaited(_loadHtmlInternal(ctrl, nextHtml, baseUrl: nextBaseUrl));
      }
      return;
    }

    final nextUrl = widget.control.getString('url', '') ?? '';
    if (shouldNavigateOnWidgetUrlSync(
      nextUrl: nextUrl,
      previousInitialUrl: _initialUrl,
      currentLoadedUrl: _currentLoadedUrl,
    )) {
      _initialUrl = nextUrl;
      final nextPrefix = extractViewerLockPrefix(nextUrl);
      if (nextPrefix != null) {
        _viewerPrefix = nextPrefix;
        _viewerLocked = true;
      }
      final uri = Uri.tryParse(nextUrl);
      if (uri != null &&
          uri.scheme.toLowerCase() == 'http' &&
          (uri.host == '127.0.0.1' || uri.host.toLowerCase() == 'localhost') &&
          uri.hasPort) {
        _allowedLoopbackPorts = {..._allowedLoopbackPorts, uri.port};
      }
      if (_renderProcessGone) {
        _remountAfterRenderProcessGone(targetUrl: nextUrl);
        return;
      }
      final ctrl = _controller;
      if (ctrl != null) {
        unawaited(_navigateInternal(ctrl, nextUrl));
      }
    } else if (nextUrl.isNotEmpty) {
      _initialUrl = nextUrl;
    }
  }

  bool _detectIsTablet() {
    if (mounted) {
      final media = context.getInheritedWidgetOfExactType<MediaQuery>();
      if (media != null && media.data.size.shortestSide > 0) {
        return media.data.size.shortestSide >= 600.0;
      }
    }
    final views = WidgetsBinding.instance.platformDispatcher.views;
    if (views.isNotEmpty) {
      final view = views.first;
      final dpr = view.devicePixelRatio > 0 ? view.devicePixelRatio : 1.0;
      return (view.physicalSize / dpr).shortestSide >= 600.0;
    }
    return false;
  }

  Future<void> _prepare() async {
    if (!kIsWeb && defaultTargetPlatform == TargetPlatform.android) {
      try {
        final defaultUa = await InAppWebViewController.getDefaultUserAgent()
            .timeout(const Duration(seconds: 3));
        _normalizedUserAgent = normalizeEmbeddedUserAgent(
          defaultUa,
          isTablet: _detectIsTablet(),
        );
      } catch (_) {
        _normalizedUserAgent = null;
      }
    }
    if (_disposed || !mounted) return;
    setState(() => _prepared = true);
  }

  void _onCookiePayload(Uint8List bytes) {
    if (_disposed || bytes.isEmpty || bytes.length > _cookiePayloadLimit) {
      return;
    }
    final copy = Uint8List.fromList(bytes);
    final waiter = _cookiePayloadWaiter;
    if (waiter != null && !waiter.isCompleted) {
      waiter.complete(copy);
      return;
    }
    _pendingCookiePayload?.fillRange(0, _pendingCookiePayload!.length, 0);
    _pendingCookiePayload = copy;
  }

  Future<Uint8List> _takeCookiePayload() async {
    final pending = _pendingCookiePayload;
    if (pending != null) {
      _pendingCookiePayload = null;
      return pending;
    }
    final waiter = Completer<Uint8List>();
    _cookiePayloadWaiter = waiter;
    try {
      return await waiter.future.timeout(appWebViewCookieWaitTimeout);
    } finally {
      if (identical(_cookiePayloadWaiter, waiter)) {
        _cookiePayloadWaiter = null;
      }
    }
  }

  Future<void> _clearAuthUserScripts(InAppWebViewController? controller) async {
    _authScriptTracker.clear();
    _deferredControllerUserScript = null;
    _initialUserScript = null;
    _initialUserScriptAllowedOrigin = null;
    if (controller == null) return;
    try {
      await controller.removeUserScriptsByGroupName(
        groupName: kAppWebViewAuthUserScriptGroup,
      );
    } catch (_) {}
  }

  Future<MobileAuthApplyResult> _applyDecodedAuthPayload(
    Map<dynamic, dynamic> decoded, {
    required String fallbackTargetUrl,
  }) async {
    final targetStr = decoded['target_url'] is String &&
            (decoded['target_url'] as String).trim().isNotEmpty
        ? (decoded['target_url'] as String).trim()
        : fallbackTargetUrl;
    final targetUri = Uri.tryParse(targetStr);
    final initialUri = Uri.tryParse(
      _initialUrl.isNotEmpty ? _initialUrl : targetStr,
    );
    final rawCookies = decoded['cookies'] is List
        ? decoded['cookies'] as List<dynamic>
        : const <dynamic>[];
    final rawInitScript = decoded['init_script'] is String
        ? decoded['init_script'] as String
        : null;
    final rawAllowedOrigin = decoded['init_script_allowed_origin'] is String
        ? decoded['init_script_allowed_origin'] as String
        : null;
    final ctrl = _renderProcessGone ? null : _controller;
    final cm = CookieManager.instance();

    final res = await applyAppWebViewAuthPayload(
      rawCookies: rawCookies,
      rawInitScript: rawInitScript,
      rawAllowedOrigin: rawAllowedOrigin,
      targetUri: targetUri,
      initialUri: initialUri,
      viewerLocked: _viewerLocked,
      blockUthLms: _blockUthLms,
      isDisposed: () => _disposed,
      disposeSignal: _disposeSignal.future,
      clearAuthUserScripts: () async {
        _authScriptTracker.clear();
        _deferredControllerUserScript = null;
        if (ctrl != null) {
          await ctrl.removeUserScriptsByGroupName(
            groupName: kAppWebViewAuthUserScriptGroup,
          );
        }
      },
      addAuthUserScript: (script) async {
        if (ctrl != null) {
          await ctrl.addUserScript(userScript: script);
        } else {
          _deferredControllerUserScript = script;
        }
      },
      deleteCookie: (webTarget, name, cookiePath, domain) => cm.deleteCookie(
        url: webTarget,
        name: name,
        path: cookiePath,
        domain: domain,
      ),
      setCookie: (webTarget, cookie) => cm.setCookie(
        url: webTarget,
        name: cookie.name,
        value: cookie.value,
        domain: cookie.domain,
        path: cookie.path,
        expiresDate: cookie.expiresDate,
        isSecure: cookie.isSecure,
        isHttpOnly: cookie.isHttpOnly,
        sameSite: cookie.sameSite,
      ),
    );
    if (res.installedScriptOrigin != null) {
      _authScriptTracker.arm(res.installedScriptOrigin!);
    } else {
      _authScriptTracker.clear();
    }
    return res;
  }

  Future<bool> _applyCookiesFromChannel() async {
    Uint8List? payload;
    var appliedCount = 0;
    var hasInstalledScript = false;
    try {
      payload = await _takeCookiePayload();
      final decoded = jsonDecode(utf8.decode(payload));
      if (decoded is! Map) return false;
      final res = await _applyDecodedAuthPayload(
        decoded,
        fallbackTargetUrl: _initialUrl,
      );
      hasInstalledScript = res.installedScriptOrigin != null;
      appliedCount = res.appliedCookies + (hasInstalledScript ? 1 : 0);
      return true;
    } catch (_) {
      return false;
    } finally {
      payload?.fillRange(0, payload.length, 0);
      if (!_cookieBootstrapDone.isCompleted) {
        _cookieBootstrapDone.complete();
      }
      if (shouldReloadAfterLateCookies(
        appliedCount: appliedCount,
        initialNavDispatched: _initialNavDispatched,
        alreadyReloaded: _lateCookieReloaded,
        disposed: _disposed,
        hasController: !_renderProcessGone && _controller != null,
        initialUrl: _initialUrl,
        currentLoadedUrl: _currentLoadedUrl,
      )) {
        _lateCookieReloaded = true;
        unawaited(
          _navigateInternal(
            _controller!,
            _initialUrl,
            preserveMatchingAuthScript: hasInstalledScript,
          ),
        );
      }
    }
  }

  Future<void> _onWebViewCreated(
    InAppWebViewController controller,
    int mountGeneration,
  ) async {
    if (_disposed || _renderProcessGone || mountGeneration != _webViewGeneration) {
      return;
    }
    _controller = controller;
    if (_initialHtml != null) {
      await _loadHtmlInternal(
        controller,
        _initialHtml!,
        baseUrl: _initialBaseUrl,
      );
      return;
    }
    if (_requireInitialCookies && !_viewerLocked && !_cookieBootstrapDone.isCompleted) {
      try {
        await _cookieBootstrapDone.future.timeout(appWebViewCookieWaitTimeout);
      } catch (_) {
        // Proceed with navigation even if cookie channel timed out.
      }
    }
    if (_disposed || _renderProcessGone || mountGeneration != _webViewGeneration) {
      return;
    }
    final deferredScript = _deferredControllerUserScript;
    if (deferredScript != null) {
      _deferredControllerUserScript = null;
      try {
        await controller.removeUserScriptsByGroupName(
          groupName: kAppWebViewAuthUserScriptGroup,
        );
        await controller.addUserScript(userScript: deferredScript);
        _authScriptTracker.arm(deferredScript.allowedOriginRules.first);
      } catch (_) {}
    }
    if (_disposed || _renderProcessGone || mountGeneration != _webViewGeneration) {
      return;
    }
    if (_initialUrl.isNotEmpty) {
      _initialNavDispatched = true;
      await _navigateInternal(
        controller,
        _initialUrl,
        preserveMatchingAuthScript: true,
      );
    }
  }

  Future<bool> _navigateInternal(
    InAppWebViewController controller,
    String targetUrl, {
    bool preserveMatchingAuthScript = false,
  }) async {
    final decision = decideAppWebViewNavigation(
      targetUrl,
      isForMainFrame: true,
      viewerLocked: _viewerLocked,
      viewerPrefix: _viewerPrefix,
      allowedLoopbackPorts: _allowedLoopbackPorts,
      blockUthLms: _blockUthLms,
    );
    final resolved = switch (decision.kind) {
      AppNavigationKind.allow => targetUrl.trim(),
      AppNavigationKind.redirectInPlace => decision.targetUrl,
      AppNavigationKind.cancel => null,
    };
    if (resolved == null || resolved.isEmpty) return false;
    final activeOrigin = _authScriptTracker.activeOrigin;
    if (activeOrigin != null) {
      final nextOrigin = extractHttpsOrigin(resolved);
      if (!preserveMatchingAuthScript || nextOrigin != activeOrigin) {
        await _clearAuthUserScripts(controller);
      }
    }
    _currentLoadedUrl = resolved;
    try {
      await controller.loadUrl(
        urlRequest: URLRequest(
          url: WebUri(resolved, forceToStringRawValue: true),
        ),
      );
      return true;
    } catch (_) {
      return false;
    }
  }

  Future<bool> _loadHtmlInternal(
    InAppWebViewController controller,
    String html, {
    String? baseUrl,
  }) async {
    if (_authScriptTracker.activeOrigin != null) {
      await _clearAuthUserScripts(controller);
    }
    WebUri? safeBase;
    if (baseUrl != null && baseUrl.trim().isNotEmpty) {
      safeBase = WebUri(baseUrl.trim());
    }
    final effectiveBase = safeBase ?? WebUri('about:blank');
    try {
      await controller.loadData(
        data: html,
        mimeType: 'text/html',
        encoding: 'utf-8',
        baseUrl: effectiveBase,
        historyUrl: effectiveBase,
      );
      return true;
    } catch (_) {
      return false;
    }
  }

  Future<void> _emitHistoryState(InAppWebViewController controller) async {
    if (_disposed) return;
    try {
      final canBack = await controller.canGoBack();
      final canFwd = await controller.canGoForward();
      if (_disposed) return;
      widget.control.triggerEvent(
        'history_change',
        jsonEncode({'can_go_back': canBack, 'can_go_forward': canFwd}),
      );
    } catch (_) {}
  }

  Future<dynamic> _invokeMethod(String name, dynamic args) async {
    if (_disposed) return false;
    switch (name) {
      case 'bootstrap_cookies':
        if (_viewerLocked ||
            !_requireInitialCookies ||
            _cookieBootstrapStarted ||
            args is! Map ||
            args['channel_id'] != _cookieChannel?.id) {
          if (!_cookieBootstrapDone.isCompleted) {
            _cookieBootstrapDone.complete();
          }
          return false;
        }
        _cookieBootstrapStarted = true;
        if (args['abort'] == true) {
          if (!_cookieBootstrapDone.isCompleted) {
            _cookieBootstrapDone.complete();
          }
          return false;
        }
        return await _applyCookiesFromChannel();

      case 'ensure_auth_channel':
        if (_viewerLocked || !mounted) return null;
        if (_cookieChannel == null) {
          final channel = FletBackend.of(context).openDataChannel();
          _cookieChannel = channel;
          _cookieSubscription = channel.messages.listen(_onCookiePayload);
          widget.control.triggerEvent('data_channel_open', {
            'channel_name': 'app_webview_cookies',
            'channel_id': channel.id,
          });
        }
        return _cookieChannel?.id;

      case 'apply_auth_and_navigate':
        if (_viewerLocked ||
            args is! Map ||
            _cookieChannel == null ||
            args['channel_id'] != _cookieChannel?.id) {
          return false;
        }
        Uint8List? payload;
        try {
          payload = await _takeCookiePayload();
          final decoded = jsonDecode(utf8.decode(payload));
          if (decoded is! Map) return false;
          final targetUrl = decoded['target_url'] is String
              ? (decoded['target_url'] as String).trim()
              : '';
          if (targetUrl.isEmpty) return false;
          await _applyDecodedAuthPayload(
            decoded,
            fallbackTargetUrl: targetUrl,
          );
          if (_disposed) return false;
          _initialHtml = null;
          _initialUrl = targetUrl;
          final authAction = resolveAppWebViewMethodDuringRenderProcessGone(
            'apply_auth_and_navigate',
            renderProcessGone: _renderProcessGone,
            hasController: _controller != null,
          );
          if (authAction == AppWebViewRenderProcessMethodOutcome.remountRecovery) {
            _remountAfterRenderProcessGone(targetUrl: targetUrl);
            return true;
          }
          final ctrl = _controller;
          if (ctrl == null) {
            return true;
          }
          return await _navigateInternal(
            ctrl,
            targetUrl,
            preserveMatchingAuthScript: true,
          );
        } catch (_) {
          return false;
        } finally {
          payload?.fillRange(0, payload.length, 0);
        }

      case 'navigate':
        final url = args is Map && args['url'] is String
            ? (args['url'] as String).trim()
            : '';
        if (url.isEmpty) return false;
        final uri = Uri.tryParse(url);
        if (!_viewerLocked &&
            uri != null &&
            uri.scheme.toLowerCase() == 'http' &&
            (uri.host == '127.0.0.1' || uri.host.toLowerCase() == 'localhost') &&
            uri.hasPort) {
          _allowedLoopbackPorts = {..._allowedLoopbackPorts, uri.port};
        }
        final navAction = resolveAppWebViewMethodDuringRenderProcessGone(
          'navigate',
          renderProcessGone: _renderProcessGone,
          hasController: _controller != null,
        );
        if (navAction == AppWebViewRenderProcessMethodOutcome.remountRecovery) {
          _initialHtml = null;
          _initialUrl = url;
          _deferredControllerUserScript = null;
          _initialUserScript = null;
          _initialUserScriptAllowedOrigin = null;
          _authScriptTracker.clear();
          _remountAfterRenderProcessGone(targetUrl: url);
          return true;
        }
        final ctrl = _controller;
        if (ctrl == null) {
          _initialHtml = null;
          _initialUrl = url;
          _deferredControllerUserScript = null;
          _initialUserScript = null;
          _initialUserScriptAllowedOrigin = null;
          _authScriptTracker.clear();
          return true;
        }
        return await _navigateInternal(ctrl, url);

      case 'load_html':
        final html =
            args is Map && args['html'] is String ? args['html'] as String : '';
        final baseUrl = args is Map && args['base_url'] is String
            ? args['base_url'] as String
            : null;
        final htmlAction = resolveAppWebViewMethodDuringRenderProcessGone(
          'load_html',
          renderProcessGone: _renderProcessGone,
          hasController: _controller != null,
        );
        if (htmlAction == AppWebViewRenderProcessMethodOutcome.remountRecovery) {
          _initialHtml = html;
          _initialBaseUrl = baseUrl;
          _deferredControllerUserScript = null;
          _initialUserScript = null;
          _initialUserScriptAllowedOrigin = null;
          _authScriptTracker.clear();
          _remountAfterRenderProcessGone(html: html, baseUrl: baseUrl);
          return true;
        }
        final ctrl = _controller;
        if (ctrl == null) {
          _initialHtml = html;
          _initialBaseUrl = baseUrl;
          _deferredControllerUserScript = null;
          _initialUserScript = null;
          _initialUserScriptAllowedOrigin = null;
          _authScriptTracker.clear();
          return true;
        }
        await _clearAuthUserScripts(ctrl);
        return await _loadHtmlInternal(ctrl, html, baseUrl: baseUrl);

      case 'eval_js':
        final script = args is Map && args['script'] is String
            ? args['script'] as String
            : '';
        final evalAction = resolveAppWebViewMethodDuringRenderProcessGone(
          'eval_js',
          renderProcessGone: _renderProcessGone,
          hasController: _controller != null,
        );
        final ctrl = _controller;
        if (evalAction == AppWebViewRenderProcessMethodOutcome.failClosed ||
            ctrl == null ||
            script.isEmpty) {
          return false;
        }
        if (_viewerLocked) {
          try {
            await ctrl.evaluateJavascript(source: script);
            return true;
          } catch (_) {
            return false;
          }
        }
        final timeout = Completer<void>();
        final timer = Timer(_cookieQueueTimeout, timeout.complete);
        MobileSessionLease? lease;
        try {
          lease = await sharedMobileSessionQueue.acquire(
            cancelled: Future.any<void>([_disposeSignal.future, timeout.future]),
          );
          if (lease == null ||
              _disposed ||
              _renderProcessGone ||
              !isSameInAppWebViewController(_controller, ctrl)) {
            return false;
          }
          await ctrl.evaluateJavascript(source: script);
          return true;
        } catch (_) {
          return false;
        } finally {
          timer.cancel();
          lease?.release();
        }

      case 'go_back':
        final backAction = resolveAppWebViewMethodDuringRenderProcessGone(
          'go_back',
          renderProcessGone: _renderProcessGone,
          hasController: _controller != null,
        );
        final ctrl = _controller;
        if (backAction == AppWebViewRenderProcessMethodOutcome.failClosed ||
            ctrl == null) {
          return false;
        }
        try {
          if (await ctrl.canGoBack()) {
            await ctrl.goBack();
            await _emitHistoryState(ctrl);
            return true;
          }
        } catch (_) {}
        return false;

      case 'go_forward':
        final fwdAction = resolveAppWebViewMethodDuringRenderProcessGone(
          'go_forward',
          renderProcessGone: _renderProcessGone,
          hasController: _controller != null,
        );
        final ctrl = _controller;
        if (fwdAction == AppWebViewRenderProcessMethodOutcome.failClosed ||
            ctrl == null) {
          return false;
        }
        try {
          if (await ctrl.canGoForward()) {
            await ctrl.goForward();
            await _emitHistoryState(ctrl);
            return true;
          }
        } catch (_) {}
        return false;

      case 'reload':
        final reloadAction = resolveAppWebViewMethodDuringRenderProcessGone(
          'reload',
          renderProcessGone: _renderProcessGone,
          hasController: _controller != null,
        );
        if (reloadAction ==
            AppWebViewRenderProcessMethodOutcome.remountRecovery) {
          if (_initialHtml != null) {
            _remountAfterRenderProcessGone(
              html: _initialHtml!,
              baseUrl: _initialBaseUrl,
            );
            return true;
          }
          final retryUrl = _resolveRenderProcessRecoveryUrl();
          if (retryUrl.isNotEmpty) {
            _remountAfterRenderProcessGone(targetUrl: retryUrl);
            return true;
          }
          return false;
        }
        final ctrl = _controller;
        if (reloadAction == AppWebViewRenderProcessMethodOutcome.failClosed ||
            ctrl == null) {
          return false;
        }
        try {
          final activeUrl = (await ctrl.getUrl())?.toString();
          if (shouldReloadViaInitialUrl(
            activeControllerUrl: activeUrl,
            initialUrl: _initialUrl,
            hasInitialHtml: _initialHtml != null,
          )) {
            return await _navigateInternal(ctrl, _initialUrl);
          }
          if (_initialHtml != null && (activeUrl == null || activeUrl.isEmpty)) {
            return await _loadHtmlInternal(
              ctrl,
              _initialHtml!,
              baseUrl: _initialBaseUrl,
            );
          }
          await ctrl.reload();
          return true;
        } catch (_) {
          return false;
        }
    }
    return false;
  }

  void _revealSurfaceIfReady(String? url) {
    if (_disposed) return;
    if (shouldRevealAppWebViewSurface(
      url: url,
      hasInitialHtml: _initialHtml != null,
      suppressAbortedRedirectCommit: _suppressCommitUntilNextLoadStart,
    )) {
      _hasCommittedPage = true;
      if (!_surfaceRevealed && mounted) {
        setState(() => _surfaceRevealed = true);
      }
    }
  }

  Future<bool> _launchNativeExternalUri(String uri) async {
    if (kIsWeb ||
        (defaultTargetPlatform != TargetPlatform.android &&
            defaultTargetPlatform != TargetPlatform.iOS)) {
      return false;
    }
    try {
      final ok = await _backgroundSyncChannel
          .invokeMethod<bool>('launch_external_intent', {'uri': uri})
          .timeout(const Duration(seconds: 3));
      return ok == true;
    } catch (_) {
      return false;
    }
  }

  Future<void> _executeExternalAppPlan(
    InAppWebViewController controller,
    ExternalAppNavigationPlan plan, {
    required bool hasGesture,
    required bool hadCommittedBeforeHandoff,
  }) async {
    final launched = await _launchNativeExternalUri(plan.launchUri);
    if (_disposed) return;
    if (launched) {
      if (!hadCommittedBeforeHandoff) {
        widget.control.triggerEvent(
          'web_error',
          '$appWebViewExternalLaunchedPrefix${plan.scheme}',
        );
      } else if (_hasCommittedPage) {
        widget.control.triggerEvent('page_ended', _currentLoadedUrl);
      }
      return;
    }

    if (hasGesture && plan.marketFallbackUri != null) {
      final storeOpened = await _launchNativeExternalUri(plan.marketFallbackUri!);
      if (_disposed) return;
      if (storeOpened) {
        if (!hadCommittedBeforeHandoff) {
          widget.control.triggerEvent(
            'web_error',
            '$appWebViewExternalLaunchedPrefix${plan.scheme}',
          );
        } else if (_hasCommittedPage) {
          widget.control.triggerEvent('page_ended', _currentLoadedUrl);
        }
        return;
      }
    }

    if (plan.httpsFallbackUrl != null && !plan.isStoreFallback) {
      final fallback = plan.httpsFallbackUrl!;
      _currentLoadedUrl = fallback;
      unawaited(
        controller
            .loadUrl(
              urlRequest: URLRequest(
                url: WebUri(fallback, forceToStringRawValue: true),
              ),
            )
            .catchError((_) {}),
      );
      return;
    }

    if (!hadCommittedBeforeHandoff) {
      widget.control.triggerEvent(
        'web_error',
        '$appWebViewExternalMissingPrefix${plan.scheme}',
      );
    } else if (_hasCommittedPage) {
      widget.control.triggerEvent('page_ended', _currentLoadedUrl);
    }
  }

  Future<bool> _handleCreateWindow(
    InAppWebViewController controller,
    CreateWindowAction action,
  ) async {
    if (_disposed) return false;
    final rawPopupUri = action.request.url;
    final rawPopupUrl = rawPopupUri?.rawValue.isNotEmpty == true
        ? rawPopupUri!.rawValue
        : rawPopupUri?.toString();
    if (!kIsWeb &&
        (defaultTargetPlatform == TargetPlatform.android ||
            defaultTargetPlatform == TargetPlatform.iOS)) {
      final extPlan = resolveExternalAppNavigation(
        rawPopupUrl,
        isForMainFrame: true,
        viewerLocked: _viewerLocked,
        currentUrl: _currentLoadedUrl.isNotEmpty ? _currentLoadedUrl : _initialUrl,
        blockUthLms: _blockUthLms,
        platform: defaultTargetPlatform,
      );
      if (extPlan != null) {
        unawaited(
          _executeExternalAppPlan(
            controller,
            extPlan,
            hasGesture: true,
            hadCommittedBeforeHandoff: true,
          ),
        );
        return false;
      }
    }
    final target = resolveCreateWindowInPlaceTarget(
      rawPopupUrl,
      viewerLocked: _viewerLocked,
      allowedLoopbackPorts: _allowedLoopbackPorts,
      blockUthLms: _blockUthLms,
    );
    if (target != null) {
      unawaited(_navigateInternal(controller, target));
    }
    // On Android, JS `window.open(url, '_blank')` arrives with `request.url == null`
    // and holds a pending `WebViewTransport` (`resultMsg`) until a child WebView
    // attaches; on iOS/Windows returning false navigates the main frame to blocked URLs.
    if (!kIsWeb && shouldAdoptAppPopupWindowSink(defaultTargetPlatform)) {
      final sinkKey = _nextPopupSinkKey++;
      final allowDeferredRedirect = target == null && !_viewerLocked;
      _popupSinkTimers[sinkKey] = Timer(
        _popupSinkLifetime,
        () => _removePopupSink(sinkKey),
      );
      setState(() {
        trimPopupSinksToCap(
          _popupSinks,
          maxCount: maxAppPopupSinks,
          onEvict: (oldest) {
            _popupSinkTimers.remove(oldest.key)?.cancel();
            _consumedPopupSinkKeys.remove(oldest.key);
          },
        );
        _popupSinks.add(
          _PopupSinkEntry(
            windowId: action.windowId,
            key: sinkKey,
            allowInPlaceRedirect: allowDeferredRedirect,
          ),
        );
      });
      return true;
    }
    return false;
  }

  void _removePopupSink(int key) {
    _popupSinkTimers.remove(key)?.cancel();
    _consumedPopupSinkKeys.remove(key);
    if (_disposed || !mounted) return;
    setState(() => _popupSinks.removeWhere((item) => item.key == key));
  }

  void _handlePopupSinkTarget(_PopupSinkEntry sink, String? rawUrl) {
    if (_disposed ||
        !sink.allowInPlaceRedirect ||
        _consumedPopupSinkKeys.contains(sink.key) ||
        isTransientPopupSinkBootstrapUrl(rawUrl)) {
      return;
    }
    if (!kIsWeb &&
        !_renderProcessGone &&
        _controller != null &&
        (defaultTargetPlatform == TargetPlatform.android ||
            defaultTargetPlatform == TargetPlatform.iOS)) {
      final extPlan = resolveExternalAppNavigation(
        rawUrl,
        isForMainFrame: true,
        viewerLocked: _viewerLocked,
        currentUrl: _currentLoadedUrl.isNotEmpty ? _currentLoadedUrl : _initialUrl,
        blockUthLms: _blockUthLms,
        platform: defaultTargetPlatform,
      );
      if (extPlan != null) {
        _consumedPopupSinkKeys.add(sink.key);
        unawaited(
          _executeExternalAppPlan(
            _controller!,
            extPlan,
            hasGesture: true,
            hadCommittedBeforeHandoff: true,
          ),
        );
        WidgetsBinding.instance.addPostFrameCallback((_) {
          _removePopupSink(sink.key);
        });
        return;
      }
    }
    final deferredTarget = resolveCreateWindowInPlaceTarget(
      rawUrl,
      viewerLocked: _viewerLocked,
      allowedLoopbackPorts: _allowedLoopbackPorts,
      blockUthLms: _blockUthLms,
    );
    if (deferredTarget != null && !_renderProcessGone && _controller != null) {
      _consumedPopupSinkKeys.add(sink.key);
      unawaited(_navigateInternal(_controller!, deferredTarget));
    }
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _removePopupSink(sink.key);
    });
  }

  String _resolveRenderProcessRecoveryUrl() {
    final current = _currentLoadedUrl.trim();
    if (current.isNotEmpty && !current.toLowerCase().startsWith('about:')) {
      return current;
    }
    return _initialUrl.trim();
  }

  void _remountAfterRenderProcessGone({
    String? targetUrl,
    String? html,
    String? baseUrl,
  }) {
    if (_disposed || !mounted) return;
    if (html != null) {
      _initialHtml = html;
      _initialBaseUrl = baseUrl;
    } else if (targetUrl != null && targetUrl.trim().isNotEmpty) {
      _initialHtml = null;
      _initialUrl = targetUrl.trim();
    }
    _controller = null;
    _hasCommittedPage = false;
    _hasReceivedDocumentTitle = false;
    _suppressCommitUntilNextLoadStart = false;
    setState(() {
      _renderProcessGone = false;
      _surfaceRevealed = false;
      _webViewGeneration++;
      _webViewKey = GlobalKey();
    });
  }

  Widget _buildPopupSink(_PopupSinkEntry sink) {
    return Offstage(
      offstage: true,
      child: SizedBox(
        width: 1,
        height: 1,
        child: InAppWebView(
          key: ValueKey('app-wv-sink-${sink.key}'),
          windowId: sink.windowId,
          initialSettings: createAppWebViewSettings(
            viewerLocked: true,
            javaScriptEnabled: false,
          ),
          onWebViewCreated: (ctrl) {
            if (!sink.allowInPlaceRedirect) {
              unawaited(ctrl.stopLoading().catchError((_) {}));
              WidgetsBinding.instance.addPostFrameCallback((_) {
                _removePopupSink(sink.key);
              });
            } else {
              _popupSinkTimers[sink.key]?.cancel();
              _popupSinkTimers[sink.key] = Timer(
                _popupSinkResolveTimeout,
                () => _removePopupSink(sink.key),
              );
            }
          },
          onLoadStart: (ctrl, url) {
            final urlStr = url?.toString();
            if (sink.allowInPlaceRedirect &&
                !_consumedPopupSinkKeys.contains(sink.key) &&
                isTransientPopupSinkBootstrapUrl(urlStr)) {
              return;
            }
            _handlePopupSinkTarget(sink, urlStr);
            unawaited(ctrl.stopLoading().catchError((_) {}));
          },
          shouldOverrideUrlLoading: (_c, action) async {
            if (action.isForMainFrame) {
              _handlePopupSinkTarget(sink, action.request.url?.toString());
            }
            return NavigationActionPolicy.CANCEL;
          },
          onCreateWindow: (_c, _a) async => false,
          onRenderProcessGone: (_controller, _detail) {
            handleAppWebViewPopupSinkRenderProcessGone(
              disposed: _disposed,
              mounted: mounted,
              removeSink: () => _removePopupSink(sink.key),
            );
          },
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final effectiveBgColor = widget.control.getColor('bgcolor', context) ??
        parseAppWebViewColor(widget.control.getString('bgcolor')) ??
        Theme.of(context).scaffoldBackgroundColor;

    if (!_prepared) {
      return LayoutControl(
        control: widget.control,
        child: ColoredBox(
          color: effectiveBgColor,
          child: const SizedBox.expand(),
        ),
      );
    }

    final mountGeneration = _webViewGeneration;

    return LayoutControl(
      control: widget.control,
      child: Stack(
        fit: StackFit.expand,
        children: [
          if (!_renderProcessGone)
            InAppWebView(
              key: _webViewKey,
              initialUserScripts: buildInitialAppWebViewUserScripts(
                source: _initialUserScript,
                allowedOrigin: _initialUserScriptAllowedOrigin,
                initialUrl: _initialUrl,
                viewerLocked: _viewerLocked,
                blockUthLms: _blockUthLms,
              ),
              initialSettings: createAppWebViewSettings(
                viewerLocked: _viewerLocked,
                viewerPrefix: _viewerPrefix,
                userAgent: _normalizedUserAgent,
                isInspectable: kDebugMode && !_viewerLocked,
              ),
              onWebViewCreated: (controller) =>
                  unawaited(_onWebViewCreated(controller, mountGeneration)),
            shouldOverrideUrlLoading: (controller, action) async {
              if (_disposed) return NavigationActionPolicy.CANCEL;
              final rawActionUri = action.request.url;
              final actionUrl =
                  rawActionUri?.rawValue.isNotEmpty == true
                      ? rawActionUri!.rawValue
                      : rawActionUri?.toString();
              final activeOrigin = _authScriptTracker.activeOrigin;
              if (action.isForMainFrame && activeOrigin != null) {
                final actionOrigin = extractHttpsOrigin(actionUrl);
                if (actionOrigin != activeOrigin) {
                  await _clearAuthUserScripts(controller);
                }
              }
              final effectiveCurrentUrl =
                  _currentLoadedUrl.isNotEmpty ? _currentLoadedUrl : _initialUrl;

              if (!kIsWeb &&
                  (defaultTargetPlatform == TargetPlatform.android ||
                      defaultTargetPlatform == TargetPlatform.iOS)) {
                final externalPlan = resolveExternalAppNavigation(
                  actionUrl,
                  isForMainFrame: action.isForMainFrame,
                  viewerLocked: _viewerLocked,
                  currentUrl: effectiveCurrentUrl,
                  blockUthLms: _blockUthLms,
                  platform: defaultTargetPlatform,
                );
                if (externalPlan != null) {
                  final preDocumentRedirect =
                      isUncommittedPreDocumentExternalRedirect(
                    hasCommittedPage: _hasCommittedPage,
                    hasDocumentTitle: _hasReceivedDocumentTitle,
                    isRedirect: action.isRedirect,
                  );
                  if (preDocumentRedirect) {
                    _suppressCommitUntilNextLoadStart = true;
                  }
                  unawaited(
                    _executeExternalAppPlan(
                      controller,
                      externalPlan,
                      hasGesture: action.hasGesture ?? false,
                      hadCommittedBeforeHandoff: !preDocumentRedirect,
                    ),
                  );
                  return NavigationActionPolicy.CANCEL;
                }
              }

              final decision = decideAppWebViewNavigation(
                actionUrl,
                isForMainFrame: action.isForMainFrame,
                viewerLocked: _viewerLocked,
                viewerPrefix: _viewerPrefix,
                allowedLoopbackPorts: _allowedLoopbackPorts,
                blockUthLms: _blockUthLms,
              );
              if (decision.kind == AppNavigationKind.redirectInPlace &&
                  decision.targetUrl != null) {
                final redirectTarget = decision.targetUrl!;
                if (!_hasCommittedPage) {
                  _suppressCommitUntilNextLoadStart = true;
                }
                _currentLoadedUrl = redirectTarget;
                Future<void>.delayed(Duration.zero, () {
                  if (_disposed) return;
                  unawaited(
                    controller
                        .loadUrl(
                          urlRequest: URLRequest(
                            url: WebUri(
                              redirectTarget,
                              forceToStringRawValue: true,
                            ),
                          ),
                        )
                        .catchError((_) {}),
                  );
                });
                return NavigationActionPolicy.CANCEL;
              }
              if (!kIsWeb &&
                  shouldUseCleanAndroidMainFrameLoad(
                    platform: defaultTargetPlatform,
                    viewerLocked: _viewerLocked,
                    isForMainFrame: action.isForMainFrame,
                    decisionKind: decision.kind,
                    rawUrl: actionUrl,
                    method: action.request.method,
                    currentUrl: effectiveCurrentUrl,
                  )) {
                final cleanUrl = actionUrl!.trim();
                if (!_hasCommittedPage) {
                  _suppressCommitUntilNextLoadStart = true;
                }
                _currentLoadedUrl = cleanUrl;
                Future<void>.delayed(Duration.zero, () {
                  if (_disposed) return;
                  unawaited(
                    controller
                        .loadUrl(
                          urlRequest: URLRequest(
                            url: WebUri(cleanUrl, forceToStringRawValue: true),
                          ),
                        )
                        .catchError((_) {}),
                  );
                });
                return NavigationActionPolicy.CANCEL;
              }
              return decision.kind == AppNavigationKind.allow
                  ? NavigationActionPolicy.ALLOW
                  : NavigationActionPolicy.CANCEL;
            },
            onCreateWindow: _handleCreateWindow,
            onDownloadStartRequest: (_controller, downloadStartRequest) {
              if (_disposed) return;
              final extUrl = resolveDownloadExternalUrl(
                downloadStartRequest.url.toString(),
                viewerLocked: _viewerLocked,
                blockUthLms: _blockUthLms,
              );
              if (extUrl != null) {
                widget.control.triggerEvent('external_url', extUrl);
              }
            },
            onLoadStart: (controller, url) {
              if (_disposed) return;
              final urlStr = url?.toString() ?? '';
              final urlLower = urlStr.toLowerCase();
              final isTransientAbout = urlLower == 'about:blank' ||
                  urlLower.startsWith('about:blank?') ||
                  urlLower.startsWith('about:blank#') ||
                  (!_viewerLocked &&
                      (urlLower == 'about:srcdoc' ||
                          urlLower.startsWith('about:srcdoc?') ||
                          urlLower.startsWith('about:srcdoc#')));
              if (urlStr.isNotEmpty && !isTransientAbout) {
                final decision = decideAppWebViewNavigation(
                  urlStr,
                  isForMainFrame: true,
                  viewerLocked: _viewerLocked,
                  viewerPrefix: _viewerPrefix,
                  allowedLoopbackPorts: _allowedLoopbackPorts,
                  blockUthLms: _blockUthLms,
                );
                if (decision.kind == AppNavigationKind.redirectInPlace &&
                    decision.targetUrl != null) {
                  unawaited(controller.stopLoading().catchError((_) {}));
                  _currentLoadedUrl = decision.targetUrl!;
                  unawaited(
                    controller
                        .loadUrl(
                          urlRequest: URLRequest(
                            url: WebUri(
                              decision.targetUrl!,
                              forceToStringRawValue: true,
                            ),
                          ),
                        )
                        .catchError((_) {}),
                  );
                  return;
                }
                if (decision.kind != AppNavigationKind.allow) {
                  unawaited(controller.stopLoading().catchError((_) {}));
                  return;
                }
                if (!urlLower.startsWith('blob:')) {
                  _currentLoadedUrl = urlStr;
                  final loadAction =
                      _authScriptTracker.onMainFrameLoadStart(urlStr);
                  if (loadAction == AuthScriptLoadAction.clearNow) {
                    unawaited(_clearAuthUserScripts(controller));
                  }
                }
                _suppressCommitUntilNextLoadStart = false;
              }
              widget.control.triggerEvent('page_started', urlStr);
              unawaited(_emitHistoryState(controller));
            },
            onPageCommitVisible: (_controller, url) {
              if (_suppressCommitUntilNextLoadStart) return;
              _revealSurfaceIfReady(url?.toString());
            },
            onLoadStop: (controller, url) async {
              if (_disposed || _suppressCommitUntilNextLoadStart) return;
              final urlStr = url?.toString() ?? '';
              if (_authScriptTracker.shouldClearOnLoadStop(
                urlStr,
                hasInitialHtml: _initialHtml != null,
              )) {
                await _clearAuthUserScripts(controller);
              }
              _revealSurfaceIfReady(urlStr);
              widget.control.triggerEvent('page_ended', urlStr);
              await _emitHistoryState(controller);
              try {
                final title = await controller.getTitle();
                if (!_disposed && title != null && title.isNotEmpty) {
                  widget.control.triggerEvent('title_change', title);
                }
              } catch (_) {}
            },
            onUpdateVisitedHistory: (controller, url, _isReload) {
              if (_disposed) return;
              final urlStr = url?.toString() ?? '';
              if (urlStr.isNotEmpty) {
                final urlLower = urlStr.toLowerCase();
                if (!urlLower.startsWith('about:') &&
                    !urlLower.startsWith('blob:')) {
                  _currentLoadedUrl = urlStr;
                }
                widget.control.triggerEvent('url_change', urlStr);
              }
              unawaited(_emitHistoryState(controller));
            },
            onTitleChanged: (_controller, title) {
              if (_disposed || title == null) return;
              final trimmedTitle = title.trim();
              if (trimmedTitle.isEmpty) return;
              final lowerTitle = trimmedTitle.toLowerCase();
              final activeLower =
                  (_currentLoadedUrl.isNotEmpty ? _currentLoadedUrl : _initialUrl)
                      .toLowerCase();
              if (lowerTitle != 'about:blank' &&
                  !lowerTitle.startsWith('http://') &&
                  !lowerTitle.startsWith('https://') &&
                  (activeLower.isEmpty || !activeLower.contains(lowerTitle))) {
                _hasReceivedDocumentTitle = true;
              }
              widget.control.triggerEvent('title_change', trimmedTitle);
            },
            onReceivedError: (_controller, request, error) {
              if (_disposed) return;
              if (shouldReportMainFrameWebError(
                isForMainFrame: request.isForMainFrame,
                errorType: error.type,
                description: error.description,
              )) {
                widget.control.triggerEvent(
                  'web_error',
                  error.description.isNotEmpty
                      ? error.description
                      : 'WebView load error',
                );
              }
            },
            onConsoleMessage: (_controller, consoleMessage) {
              if (_disposed) return;
              final msg = consoleMessage.message;
              if (shouldForwardAppConsoleMessage(
                msg,
                viewerLocked: _viewerLocked,
              )) {
                widget.control.triggerEvent('console_message', msg);
              }
            },
            onRenderProcessGone: (controller, detail) {
              handleAppWebViewRenderProcessGone(
                disposed: _disposed,
                mounted: mounted,
                callbackGeneration: mountGeneration,
                activeGeneration: _webViewGeneration,
                detail: detail,
                clearDeadWebViewState: () {
                  if (_controller == null ||
                      isSameInAppWebViewController(_controller, controller)) {
                    _controller = null;
                  }
                  _webViewGeneration++;
                  _hasCommittedPage = false;
                  _hasReceivedDocumentTitle = false;
                  _suppressCommitUntilNextLoadStart = false;
                  setState(() {
                    _renderProcessGone = true;
                    _surfaceRevealed = false;
                  });
                },
                reportWebError: (err) {
                  widget.control.triggerEvent('web_error', err);
                },
              );
            },
          ),
          if (_renderProcessGone)
            Material(
              color: effectiveBgColor,
              child: Center(
                child: Padding(
                  padding: const EdgeInsets.all(24),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      const Icon(Icons.refresh_rounded, size: 32),
                      const SizedBox(height: 12),
                      const Text(
                        'Trình xem web vừa được hệ thống khởi động lại.',
                        textAlign: TextAlign.center,
                      ),
                      const SizedBox(height: 12),
                      OutlinedButton(
                        onPressed: () {
                          if (_initialHtml != null) {
                            _remountAfterRenderProcessGone(
                              html: _initialHtml!,
                              baseUrl: _initialBaseUrl,
                            );
                          } else {
                            _remountAfterRenderProcessGone(
                              targetUrl: _resolveRenderProcessRecoveryUrl(),
                            );
                          }
                        },
                        child: const Text('Tải lại'),
                      ),
                    ],
                  ),
                ),
              ),
            )
          else if (!_surfaceRevealed)
            IgnorePointer(
              child: ColoredBox(
                color: effectiveBgColor,
                child: const SizedBox.expand(),
              ),
            ),
          for (final sink in _popupSinks) _buildPopupSink(sink),
        ],
      ),
    );
  }

  @override
  void dispose() {
    _disposed = true;
    if (!_disposeSignal.isCompleted) {
      _disposeSignal.complete();
    }
    widget.control.removeInvokeMethodListener(_invokeMethod);
    _cookieSubscription?.cancel();
    _cookieSubscription = null;
    _cookieChannel?.close();
    _cookieChannel = null;
    _pendingCookiePayload?.fillRange(0, _pendingCookiePayload!.length, 0);
    _pendingCookiePayload = null;
    final waiter = _cookiePayloadWaiter;
    if (waiter != null && !waiter.isCompleted) {
      waiter.completeError(StateError('disposed'));
    }
    if (!_cookieBootstrapDone.isCompleted) {
      _cookieBootstrapDone.complete();
    }
    for (final timer in _popupSinkTimers.values) {
      timer.cancel();
    }
    _popupSinkTimers.clear();
    super.dispose();
  }
}
