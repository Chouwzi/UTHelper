import 'dart:async';
import 'dart:convert';

import 'package:flet/flet.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_inappwebview/flutter_inappwebview.dart';

import 'app_webview_settings.dart';

const _cookiePayloadLimit = 32 * 1024;
const _maxInjectedCookies = 64;
const _cookieWaitTimeout = Duration(seconds: 4);
const _popupSinkLifetime = Duration(seconds: 4);
const _uthMsgPrefix = '__UTH_MSG__:';

class _PopupSinkEntry {
  const _PopupSinkEntry({required this.windowId, required this.key});

  final int windowId;
  final int key;
}

class UthAppWebViewControl extends StatefulWidget {
  const UthAppWebViewControl({super.key, required this.control});

  final Control control;

  @override
  State<UthAppWebViewControl> createState() => _UthAppWebViewControlState();
}

class _UthAppWebViewControlState extends State<UthAppWebViewControl> {
  final GlobalKey _webViewKey = GlobalKey();
  InAppWebViewController? _controller;
  String _initialUrl = '';
  String? _initialHtml;
  String? _initialBaseUrl;
  bool _viewerLocked = false;
  String? _viewerPrefix;
  Set<int> _allowedLoopbackPorts = const <int>{};
  bool _requireInitialCookies = false;
  String? _normalizedUserAgent;
  bool _prepared = false;
  bool _disposed = false;

  DataChannel? _cookieChannel;
  StreamSubscription<Uint8List>? _cookieSubscription;
  Uint8List? _pendingCookiePayload;
  Completer<Uint8List>? _cookiePayloadWaiter;
  final Completer<void> _cookieBootstrapDone = Completer<void>();

  int _nextPopupSinkKey = 0;
  final List<_PopupSinkEntry> _popupSinks = [];
  final Map<int, Timer> _popupSinkTimers = {};

  @override
  void initState() {
    super.initState();
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
    if (_viewerPrefix != null) {
      _viewerLocked = true;
    }

    widget.control.addInvokeMethodListener(_invokeMethod);
    unawaited(_prepare());
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_cookieChannel != null) return;
    final channel = FletBackend.of(context).openDataChannel();
    _cookieChannel = channel;
    _cookieSubscription = channel.messages.listen(_onCookiePayload);
    widget.control.triggerEvent('data_channel_open', {
      'channel_name': 'app_webview_cookies',
      'channel_id': channel.id,
    });
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
      return await waiter.future.timeout(_cookieWaitTimeout);
    } finally {
      if (identical(_cookiePayloadWaiter, waiter)) {
        _cookiePayloadWaiter = null;
      }
    }
  }

  Future<bool> _applyCookiesFromChannel() async {
    Uint8List? payload;
    try {
      payload = await _takeCookiePayload();
      final decoded = jsonDecode(utf8.decode(payload));
      if (decoded is! Map) return false;
      final targetStr = decoded['target_url'] is String
          ? (decoded['target_url'] as String).trim()
          : _initialUrl;
      final targetUri = Uri.tryParse(targetStr);
      final rawCookies = decoded['cookies'];
      if (rawCookies is List &&
          rawCookies.isNotEmpty &&
          rawCookies.length <= _maxInjectedCookies &&
          targetUri != null &&
          targetUri.scheme.toLowerCase() == 'https') {
        final cm = CookieManager.instance();
        final webTarget = WebUri('${targetUri.scheme}://${targetUri.host}/');
        for (final raw in rawCookies) {
          if (_disposed) return false;
          final cookie = validateAppWebViewCookie(raw, targetUri);
          if (cookie == null) continue;
          try {
            await cm.setCookie(
              url: webTarget,
              name: cookie.name,
              value: cookie.value,
              domain: cookie.domain,
              path: cookie.path,
              expiresDate: cookie.expiresDate,
              isSecure: cookie.isSecure,
              isHttpOnly: cookie.isHttpOnly,
              sameSite: cookie.sameSite,
            );
          } catch (_) {
            // Continue setting remaining cookies cleanly.
          }
        }
      }
      return true;
    } catch (_) {
      return false;
    } finally {
      payload?.fillRange(0, payload.length, 0);
      if (!_cookieBootstrapDone.isCompleted) {
        _cookieBootstrapDone.complete();
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
    if (_requireInitialCookies && !_cookieBootstrapDone.isCompleted) {
      try {
        await _cookieBootstrapDone.future.timeout(_cookieWaitTimeout);
      } catch (_) {
        // Proceed with navigation even if cookie channel timed out.
      }
    }
    if (_disposed) return;
    if (_initialUrl.isNotEmpty) {
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
    try {
      await controller.loadData(
        data: html,
        mimeType: 'text/html',
        encoding: 'utf-8',
        baseUrl: safeBase,
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
        if (args is! Map || args['channel_id'] != _cookieChannel?.id) {
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
    final rawTarget = action.request.url?.toString();
    if (!_viewerLocked && rawTarget != null && rawTarget.isNotEmpty) {
      final decision = decideAppWebViewNavigation(
        rawTarget,
        isForMainFrame: true,
        viewerLocked: false,
        allowedLoopbackPorts: _allowedLoopbackPorts,
      );
      final target = switch (decision.kind) {
        AppNavigationKind.allow => rawTarget.trim(),
        AppNavigationKind.redirectInPlace => decision.targetUrl,
        AppNavigationKind.cancel => null,
      };
      if (target != null && target.isNotEmpty) {
        unawaited(
          controller
              .loadUrl(urlRequest: URLRequest(url: WebUri(target)))
              .catchError((_) {}),
        );
      }
    }
    // On iOS/Windows returning false from onCreateWindow would otherwise navigate
    // the main frame to even blocked URLs; sink the child windowId cleanly.
    if (!kIsWeb &&
        (defaultTargetPlatform == TargetPlatform.iOS ||
            defaultTargetPlatform == TargetPlatform.windows)) {
      final sinkKey = _nextPopupSinkKey++;
      _popupSinkTimers[sinkKey] = Timer(
        _popupSinkLifetime,
        () => _removePopupSink(sinkKey),
      );
      setState(() {
        _popupSinks.add(
          _PopupSinkEntry(windowId: action.windowId, key: sinkKey),
        );
      });
      return true;
    }
    return false;
  }

  void _removePopupSink(int key) {
    _popupSinkTimers.remove(key)?.cancel();
    if (_disposed || !mounted) return;
    setState(() => _popupSinks.removeWhere((item) => item.key == key));
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
          initialSettings: createAppWebViewSettings(viewerLocked: true),
          onWebViewCreated: (ctrl) {
            unawaited(ctrl.stopLoading().catchError((_) {}));
            WidgetsBinding.instance.addPostFrameCallback((_) {
              _removePopupSink(sink.key);
            });
          },
          onLoadStart: (ctrl, _u) =>
              unawaited(ctrl.stopLoading().catchError((_) {})),
          shouldOverrideUrlLoading: (_c, _a) async =>
              NavigationActionPolicy.CANCEL,
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
              userAgent: _normalizedUserAgent,
            ),
            onWebViewCreated: _onWebViewCreated,
            shouldOverrideUrlLoading: (controller, action) async {
              if (_disposed) return NavigationActionPolicy.CANCEL;
              final decision = decideAppWebViewNavigation(
                action.request.url?.toString(),
                isForMainFrame: action.isForMainFrame,
                viewerLocked: _viewerLocked,
                viewerPrefix: _viewerPrefix,
                allowedLoopbackPorts: _allowedLoopbackPorts,
              );
              if (decision.kind == AppNavigationKind.redirectInPlace &&
                  decision.targetUrl != null) {
                unawaited(
                  controller
                      .loadUrl(
                        urlRequest: URLRequest(url: WebUri(decision.targetUrl!)),
                      )
                      .catchError((_) {}),
                );
                return NavigationActionPolicy.CANCEL;
              }
              return decision.kind == AppNavigationKind.allow
                  ? NavigationActionPolicy.ALLOW
                  : NavigationActionPolicy.CANCEL;
            },
            onCreateWindow: _handleCreateWindow,
            onLoadStart: (controller, url) {
              if (_disposed) return;
              final urlStr = url?.toString() ?? '';
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
              if (msg.startsWith(_uthMsgPrefix)) {
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
