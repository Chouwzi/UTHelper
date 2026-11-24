import 'dart:async';
import 'dart:convert';

import 'package:flet/flet.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_inappwebview/flutter_inappwebview.dart';

import 'app_webview_settings.dart';
import 'mobile_session_queue.dart';

const _cookiePayloadLimit = 32 * 1024;
const _maxInjectedCookies = 64;
const appWebViewCookieWaitTimeout = Duration(seconds: 4);
const _cookieQueueTimeout = Duration(seconds: 5);
const _popupSinkLifetime = Duration(seconds: 4);
const _popupSinkResolveTimeout = Duration(milliseconds: 800);

/// Serializes host-only cookie installation through [MobileSessionQueue] so
/// writes never interleave with logout cleanup or quiz origin purges, and pins
/// cookie targets strictly to [initialUri]'s exact HTTPS host and port.
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
  if (isDisposed() ||
      rawCookies.isEmpty ||
      rawCookies.length > _maxInjectedCookies ||
      initialUri == null ||
      initialUri.scheme.toLowerCase() != 'https' ||
      initialUri.host.isEmpty ||
      targetUri == null ||
      targetUri.scheme.toLowerCase() != 'https' ||
      targetUri.host.toLowerCase() != initialUri.host.toLowerCase() ||
      targetUri.port != initialUri.port) {
    return 0;
  }

  final validated = <ValidatedAppCookie>[];
  for (final raw in rawCookies) {
    final cookie = validateAppWebViewCookie(raw, targetUri);
    if (cookie != null) {
      validated.add(cookie);
    }
  }
  if (validated.isEmpty || isDisposed()) {
    return 0;
  }

  final sessionQueue = queue ?? sharedMobileSessionQueue;
  final timeout = Completer<void>();
  final timer = Timer(queueTimeout, timeout.complete);
  MobileSessionLease? lease;
  var applied = 0;
  try {
    lease = await sessionQueue.acquire(
      cancelled: Future.any<void>([disposeSignal, timeout.future]),
    );
    if (lease == null || isDisposed()) {
      return 0;
    }
    final webTarget = WebUri('${targetUri.scheme}://${targetUri.host}/');
    for (final cookie in validated) {
      if (isDisposed()) return applied;
      try {
        await setCookie(webTarget, cookie);
        applied++;
      } catch (_) {
        // Continue setting remaining cookies cleanly.
      }
    }
    return applied;
  } finally {
    timer.cancel();
    lease?.release();
  }
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

class UthAppWebViewControl extends StatefulWidget {
  const UthAppWebViewControl({super.key, required this.control});

  final Control control;

  @override
  State<UthAppWebViewControl> createState() => _UthAppWebViewControlState();
}

class _UthAppWebViewControlState extends State<UthAppWebViewControl> {
  GlobalKey _webViewKey = GlobalKey();
  InAppWebViewController? _controller;
  String _initialUrl = '';
  String _currentLoadedUrl = '';
  String? _initialHtml;
  String? _initialBaseUrl;
  bool _viewerLocked = false;
  String? _viewerPrefix;
  Set<int> _allowedLoopbackPorts = const <int>{};
  bool _requireInitialCookies = false;
  String? _normalizedUserAgent;
  bool _prepared = false;
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
    _viewerLocked = widget.control.getBool('viewer_locked', false) ?? false;
    _requireInitialCookies =
        widget.control.getBool('require_initial_cookies', false) ?? false;

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
      _cookieBootstrapStarted = false;
      _initialNavDispatched = false;
      _lateCookieReloaded = false;
      _webViewKey = GlobalKey();
      _readControlProperties();
      _openCookieChannelIfNeeded();
      return;
    }

    _requireInitialCookies =
        widget.control.getBool('require_initial_cookies', false) ?? false;
    final nextHtml = widget.control.getString('html');
    final nextBaseUrl = widget.control.getString('base_url');
    if (nextHtml != null && nextHtml != _initialHtml) {
      _initialHtml = nextHtml;
      _initialBaseUrl = nextBaseUrl;
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
      final ctrl = _controller;
      if (ctrl != null) {
        unawaited(_navigateInternal(ctrl, nextUrl));
      }
    } else if (nextUrl.isNotEmpty) {
      _initialUrl = nextUrl;
    }
  }

  Future<void> _prepare() async {
    if (!kIsWeb && defaultTargetPlatform == TargetPlatform.android) {
      try {
        final defaultUa = await InAppWebViewController.getDefaultUserAgent()
            .timeout(const Duration(seconds: 3));
        _normalizedUserAgent = normalizeEmbeddedUserAgent(defaultUa);
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

  Future<bool> _applyCookiesFromChannel() async {
    Uint8List? payload;
    var appliedCount = 0;
    try {
      payload = await _takeCookiePayload();
      final decoded = jsonDecode(utf8.decode(payload));
      if (decoded is! Map) return false;
      final targetStr = decoded['target_url'] is String
          ? (decoded['target_url'] as String).trim()
          : _initialUrl;
      final targetUri = Uri.tryParse(targetStr);
      final initialUri = Uri.tryParse(_initialUrl);
      final rawCookies = decoded['cookies'];
      if (rawCookies is List && rawCookies.isNotEmpty) {
        final cm = CookieManager.instance();
        appliedCount = await installAppWebViewCookies(
          rawCookies: rawCookies,
          targetUri: targetUri,
          initialUri: initialUri,
          isDisposed: () => _disposed,
          disposeSignal: _disposeSignal.future,
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
      }
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
        hasController: _controller != null,
        initialUrl: _initialUrl,
        currentLoadedUrl: _currentLoadedUrl,
      )) {
        _lateCookieReloaded = true;
        unawaited(_navigateInternal(_controller!, _initialUrl));
      }
    }
  }

  Future<void> _onWebViewCreated(InAppWebViewController controller) async {
    if (_disposed) return;
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
    if (_disposed) return;
    if (_initialUrl.isNotEmpty) {
      _initialNavDispatched = true;
      await _navigateInternal(controller, _initialUrl);
    }
  }

  Future<bool> _navigateInternal(
    InAppWebViewController controller,
    String targetUrl,
  ) async {
    final decision = decideAppWebViewNavigation(
      targetUrl,
      isForMainFrame: true,
      viewerLocked: _viewerLocked,
      viewerPrefix: _viewerPrefix,
      allowedLoopbackPorts: _allowedLoopbackPorts,
    );
    final resolved = switch (decision.kind) {
      AppNavigationKind.allow => targetUrl.trim(),
      AppNavigationKind.redirectInPlace => decision.targetUrl,
      AppNavigationKind.cancel => null,
    };
    if (resolved == null || resolved.isEmpty) return false;
    _currentLoadedUrl = resolved;
    try {
      await controller.loadUrl(urlRequest: URLRequest(url: WebUri(resolved)));
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
        final ctrl = _controller;
        if (ctrl == null) {
          _initialHtml = null;
          _initialUrl = url;
          return true;
        }
        return await _navigateInternal(ctrl, url);

      case 'load_html':
        final html =
            args is Map && args['html'] is String ? args['html'] as String : '';
        final baseUrl = args is Map && args['base_url'] is String
            ? args['base_url'] as String
            : null;
        final ctrl = _controller;
        if (ctrl == null) {
          _initialHtml = html;
          _initialBaseUrl = baseUrl;
          return true;
        }
        return await _loadHtmlInternal(ctrl, html, baseUrl: baseUrl);

      case 'eval_js':
        final script = args is Map && args['script'] is String
            ? args['script'] as String
            : '';
        final ctrl = _controller;
        if (ctrl == null || script.isEmpty) return false;
        try {
          await ctrl.evaluateJavascript(source: script);
          return true;
        } catch (_) {
          return false;
        }

      case 'go_back':
        final ctrl = _controller;
        if (ctrl == null) return false;
        try {
          if (await ctrl.canGoBack()) {
            await ctrl.goBack();
            await _emitHistoryState(ctrl);
            return true;
          }
        } catch (_) {}
        return false;

      case 'go_forward':
        final ctrl = _controller;
        if (ctrl == null) return false;
        try {
          if (await ctrl.canGoForward()) {
            await ctrl.goForward();
            await _emitHistoryState(ctrl);
            return true;
          }
        } catch (_) {}
        return false;

      case 'reload':
        final ctrl = _controller;
        if (ctrl == null) return false;
        try {
          await ctrl.reload();
          return true;
        } catch (_) {
          return false;
        }
    }
    return false;
  }

  Future<bool> _handleCreateWindow(
    InAppWebViewController controller,
    CreateWindowAction action,
  ) async {
    if (_disposed) return false;
    final target = resolveCreateWindowInPlaceTarget(
      action.request.url?.toString(),
      viewerLocked: _viewerLocked,
      allowedLoopbackPorts: _allowedLoopbackPorts,
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
        _consumedPopupSinkKeys.contains(sink.key)) {
      return;
    }
    final deferredTarget = resolveCreateWindowInPlaceTarget(
      rawUrl,
      viewerLocked: _viewerLocked,
      allowedLoopbackPorts: _allowedLoopbackPorts,
    );
    if (deferredTarget != null && _controller != null) {
      _consumedPopupSinkKeys.add(sink.key);
      unawaited(_navigateInternal(_controller!, deferredTarget));
    }
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _removePopupSink(sink.key);
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
            _handlePopupSinkTarget(sink, url?.toString());
            unawaited(ctrl.stopLoading().catchError((_) {}));
          },
          shouldOverrideUrlLoading: (_c, action) async {
            if (action.isForMainFrame) {
              _handlePopupSinkTarget(sink, action.request.url?.toString());
            }
            return NavigationActionPolicy.CANCEL;
          },
          onCreateWindow: (_c, _a) async => false,
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    if (!_prepared) {
      return LayoutControl(
        control: widget.control,
        child: const SizedBox.expand(),
      );
    }

    return LayoutControl(
      control: widget.control,
      child: Stack(
        fit: StackFit.expand,
        children: [
          InAppWebView(
            key: _webViewKey,
            initialSettings: createAppWebViewSettings(
              viewerLocked: _viewerLocked,
              viewerPrefix: _viewerPrefix,
              userAgent: _normalizedUserAgent,
            ),
            onWebViewCreated: _onWebViewCreated,
            shouldOverrideUrlLoading: (controller, action) async {
              if (_disposed) return NavigationActionPolicy.CANCEL;
              final actionUrl = action.request.url?.toString();
              final decision = decideAppWebViewNavigation(
                actionUrl,
                isForMainFrame: action.isForMainFrame,
                viewerLocked: _viewerLocked,
                viewerPrefix: _viewerPrefix,
                allowedLoopbackPorts: _allowedLoopbackPorts,
              );
              if (decision.kind == AppNavigationKind.redirectInPlace &&
                  decision.targetUrl != null) {
                _currentLoadedUrl = decision.targetUrl!;
                unawaited(
                  controller
                      .loadUrl(
                        urlRequest: URLRequest(url: WebUri(decision.targetUrl!)),
                      )
                      .catchError((_) {}),
                );
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
                  )) {
                final cleanUrl = actionUrl!.trim();
                _currentLoadedUrl = cleanUrl;
                unawaited(
                  controller
                      .loadUrl(urlRequest: URLRequest(url: WebUri(cleanUrl)))
                      .catchError((_) {}),
                );
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
                );
                if (decision.kind == AppNavigationKind.redirectInPlace &&
                    decision.targetUrl != null) {
                  unawaited(controller.stopLoading().catchError((_) {}));
                  _currentLoadedUrl = decision.targetUrl!;
                  unawaited(
                    controller
                        .loadUrl(
                          urlRequest:
                              URLRequest(url: WebUri(decision.targetUrl!)),
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
                }
              }
              widget.control.triggerEvent('page_started', urlStr);
              unawaited(_emitHistoryState(controller));
            },
            onLoadStop: (controller, url) async {
              if (_disposed) return;
              final urlStr = url?.toString() ?? '';
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
              if (_disposed || title == null || title.isEmpty) return;
              widget.control.triggerEvent('title_change', title);
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
