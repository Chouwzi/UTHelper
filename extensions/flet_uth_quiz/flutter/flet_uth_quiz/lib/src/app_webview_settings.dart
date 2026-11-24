import 'package:flutter_inappwebview/flutter_inappwebview.dart';

import 'quiz_popup_policy.dart' show hasUnsafeUrlCharacters;

final _cookieNamePattern = RegExp(r"^[!#\$%&'*+.^_`|~0-9A-Za-z-]+$");
final _rawAuthorityPattern = RegExp(
  r'^[a-z][a-z0-9+.-]*://([^/?#]+)',
  caseSensitive: false,
);
final _androidWvVersionPattern = RegExp(r'\bVersion/\d+(?:\.\d+)*\s+');
final _multiSpacePattern = RegExp(r' {2,}');

enum AppNavigationKind {
  allow,
  cancel,
  redirectInPlace,
}

class AppNavigationDecision {
  const AppNavigationDecision._(this.kind, [this.targetUrl]);

  const AppNavigationDecision.allow() : this._(AppNavigationKind.allow);

  const AppNavigationDecision.cancel() : this._(AppNavigationKind.cancel);

  const AppNavigationDecision.redirectInPlace(String targetUrl)
      : this._(AppNavigationKind.redirectInPlace, targetUrl);

  final AppNavigationKind kind;
  final String? targetUrl;
}

class ValidatedAppCookie {
  const ValidatedAppCookie({
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

/// Strips Android WebView `; wv` / `Version/x.y` markers so Microsoft SSO,
/// SharePoint Online, and Google treat the embedded browser like Chrome Mobile,
/// while preserving iOS Mobile Safari and desktop User-Agent strings.
String? normalizeEmbeddedUserAgent(String? rawUa) {
  if (rawUa == null) return null;
  final trimmed = rawUa.trim();
  if (trimmed.isEmpty) return null;
  if (!trimmed.contains('Android')) {
    return trimmed;
  }
  final cleaned = trimmed
      .replaceAll('; wv)', ')')
      .replaceAll('; wv;', ';')
      .replaceAll(_androidWvVersionPattern, '')
      .replaceAll(_multiSpacePattern, ' ')
      .trim();
  return cleaned.isEmpty ? null : cleaned;
}

InAppWebViewSettings createAppWebViewSettings({
  required bool viewerLocked,
  String? userAgent,
}) {
  return InAppWebViewSettings(
    javaScriptEnabled: true,
    domStorageEnabled: true,
    databaseEnabled: true,
    thirdPartyCookiesEnabled: !viewerLocked,
    supportMultipleWindows: !viewerLocked,
    javaScriptCanOpenWindowsAutomatically: !viewerLocked,
    useShouldOverrideUrlLoading: true,
    useHybridComposition: true,
    hardwareAcceleration: true,
    cacheEnabled: !viewerLocked,
    cacheMode: viewerLocked ? CacheMode.LOAD_NO_CACHE : CacheMode.LOAD_DEFAULT,
    incognito: false,
    saveFormData: false,
    isInspectable: false,
    allowFileAccess: false,
    allowContentAccess: false,
    allowFileAccessFromFileURLs: false,
    allowUniversalAccessFromFileURLs: false,
    geolocationEnabled: false,
    mixedContentMode: MixedContentMode.MIXED_CONTENT_NEVER_ALLOW,
    mediaPlaybackRequiresUserGesture: false,
    transparentBackground: false,
    offscreenPreRaster: false,
    userAgent: (userAgent != null && userAgent.isNotEmpty) ? userAgent : null,
  );
}

String? extractViewerLockPrefix(String? rawUrl) {
  if (rawUrl == null || rawUrl.isEmpty || hasUnsafeUrlCharacters(rawUrl)) {
    return null;
  }
  final uri = Uri.tryParse(rawUrl.trim());
  if (uri == null ||
      uri.scheme.toLowerCase() != 'http' ||
      !uri.hasPort ||
      uri.port < 1 ||
      uri.port > 65535) {
    return null;
  }
  final host = uri.host.toLowerCase();
  if (host != '127.0.0.1' && host != 'localhost') {
    return null;
  }
  final segs = uri.pathSegments.where((s) => s.isNotEmpty).toList();
  if (segs.length >= 2 &&
      (segs[0] == 'viewer' || segs[0] == 'pdf-viewer')) {
    return 'http://$host:${uri.port}/${segs[0]}/${segs[1]}/';
  }
  return null;
}

bool _authorityHasUserInfo(String rawUrl) {
  final rawAuthority = _rawAuthorityPattern.firstMatch(rawUrl)?.group(1);
  if (rawAuthority == null || rawAuthority.isEmpty) return true;
  return rawAuthority.contains('@');
}

bool _isValidHttpsBrowserUrl(String rawUrl) {
  if (rawUrl.isEmpty ||
      hasUnsafeUrlCharacters(rawUrl) ||
      rawUrl.contains(RegExp(r'\s')) ||
      _authorityHasUserInfo(rawUrl)) {
    return false;
  }
  final uri = Uri.tryParse(rawUrl);
  if (uri == null ||
      !uri.isAbsolute ||
      !uri.hasAuthority ||
      uri.scheme.toLowerCase() != 'https' ||
      uri.host.isEmpty ||
      uri.userInfo.isNotEmpty) {
    return false;
  }
  return true;
}

/// Extracts `S.browser_fallback_url` from Android `intent://...#Intent;...;end`
/// redirects emitted by SharePoint, OneDrive, Google Drive, or Zalo, returning
/// it only when it is a safe `https://` URL.
String? resolveAndroidIntentFallbackUrl(String? rawUrl) {
  if (rawUrl == null) return null;
  final trimmed = rawUrl.trim();
  if (trimmed.isEmpty ||
      hasUnsafeUrlCharacters(trimmed) ||
      !trimmed.toLowerCase().startsWith('intent://')) {
    return null;
  }
  final markerIndex = trimmed.toLowerCase().indexOf('#intent;');
  if (markerIndex < 0) return null;
  final tail = trimmed.substring(markerIndex + '#Intent;'.length);
  for (final rawPart in tail.split(';')) {
    if (rawPart.startsWith('S.browser_fallback_url=')) {
      final encoded = rawPart.substring('S.browser_fallback_url='.length);
      if (encoded.isEmpty) return null;
      try {
        final decoded = Uri.decodeComponent(encoded).trim();
        if (_isValidHttpsBrowserUrl(decoded)) {
          return decoded;
        }
      } catch (_) {
        return null;
      }
      return null;
    }
  }
  return null;
}

AppNavigationDecision decideAppWebViewNavigation(
  String? rawUrl, {
  required bool isForMainFrame,
  required bool viewerLocked,
  String? viewerPrefix,
  Set<int> allowedLoopbackPorts = const <int>{},
}) {
  if (rawUrl == null) return const AppNavigationDecision.cancel();
  final trimmed = rawUrl.trim();
  if (trimmed.isEmpty ||
      hasUnsafeUrlCharacters(trimmed) ||
      trimmed.contains(RegExp(r'\s'))) {
    return const AppNavigationDecision.cancel();
  }

  final lowered = trimmed.toLowerCase();
  if (lowered == 'about:blank') {
    return const AppNavigationDecision.allow();
  }

  if (lowered.startsWith('blob:')) {
    final inner = trimmed.substring(5);
    if (viewerLocked) {
      if (viewerPrefix != null && viewerPrefix.isNotEmpty) {
        final originEnd = viewerPrefix.indexOf('/', 'http://'.length);
        final origin =
            originEnd > 0 ? viewerPrefix.substring(0, originEnd + 1) : viewerPrefix;
        if (inner.toLowerCase().startsWith(origin.toLowerCase())) {
          return const AppNavigationDecision.allow();
        }
      }
      return const AppNavigationDecision.cancel();
    }
    if (inner.toLowerCase().startsWith('https://')) {
      return const AppNavigationDecision.allow();
    }
    final innerUri = Uri.tryParse(inner);
    if (innerUri != null &&
        innerUri.scheme.toLowerCase() == 'http' &&
        (innerUri.host == '127.0.0.1' ||
            innerUri.host.toLowerCase() == 'localhost') &&
        innerUri.hasPort &&
        allowedLoopbackPorts.contains(innerUri.port)) {
      return const AppNavigationDecision.allow();
    }
    return const AppNavigationDecision.cancel();
  }

  if (viewerLocked) {
    if (viewerPrefix == null || viewerPrefix.isEmpty) {
      return const AppNavigationDecision.cancel();
    }
    final uri = Uri.tryParse(trimmed);
    if (uri == null ||
        !uri.isAbsolute ||
        !uri.hasAuthority ||
        uri.userInfo.isNotEmpty ||
        uri.path.split('/').contains('..')) {
      return const AppNavigationDecision.cancel();
    }
    final normalized =
        '${uri.scheme.toLowerCase()}://${uri.host.toLowerCase()}:${uri.port}${uri.path}';
    return normalized.startsWith(viewerPrefix.toLowerCase())
        ? const AppNavigationDecision.allow()
        : const AppNavigationDecision.cancel();
  }

  if (lowered.startsWith('intent://')) {
    if (isForMainFrame) {
      final fallback = resolveAndroidIntentFallbackUrl(trimmed);
      if (fallback != null) {
        return AppNavigationDecision.redirectInPlace(fallback);
      }
    }
    return const AppNavigationDecision.cancel();
  }

  if (_isValidHttpsBrowserUrl(trimmed)) {
    return const AppNavigationDecision.allow();
  }

  final uri = Uri.tryParse(trimmed);
  if (uri != null &&
      uri.isAbsolute &&
      uri.scheme.toLowerCase() == 'http' &&
      !_authorityHasUserInfo(trimmed) &&
      uri.userInfo.isEmpty &&
      (uri.host == '127.0.0.1' || uri.host.toLowerCase() == 'localhost') &&
      uri.hasPort &&
      allowedLoopbackPorts.contains(uri.port)) {
    return const AppNavigationDecision.allow();
  }

  return const AppNavigationDecision.cancel();
}

ValidatedAppCookie? validateAppWebViewCookie(dynamic raw, Uri? targetUri) {
  if (targetUri == null ||
      targetUri.scheme.toLowerCase() != 'https' ||
      targetUri.host.isEmpty ||
      raw is! Map) {
    return null;
  }

  final name = raw['name'];
  final value = raw['value'];
  final rawDomain = raw['domain'];
  final rawPath = raw['path'] ?? '/';
  final rawSecure = raw['secure'] ?? true;
  final rawHttpOnly = raw['httpOnly'] ?? raw['http_only'] ?? false;
  final rawExpires = raw['expires'];
  final rawSameSite = raw['sameSite'] ?? raw['same_site'];

  if (name is! String ||
      !_cookieNamePattern.hasMatch(name) ||
      value is! String ||
      value.isEmpty ||
      value.length > 4096 ||
      value.contains(RegExp(r'[\x00-\x1f\x7f;]')) ||
      (rawDomain != null && rawDomain is! String) ||
      rawPath is! String ||
      !rawPath.startsWith('/') ||
      rawPath.contains(RegExp(r'[\x00-\x1f\x7f]')) ||
      rawSecure is! bool ||
      rawHttpOnly is! bool) {
    return null;
  }

  final targetHost = targetUri.host.toLowerCase();
  String? resolvedDomain;
  if (rawDomain is String && rawDomain.trim().isNotEmpty) {
    var d = rawDomain.trim().toLowerCase();
    if (d.endsWith('.local')) {
      d = d.substring(0, d.length - '.local'.length);
    }
    if (d.startsWith('.')) {
      final clean = d.substring(1);
      if (!clean.contains('.') ||
          clean.startsWith('.') ||
          clean.endsWith('.') ||
          (targetHost != clean && !targetHost.endsWith('.$clean'))) {
        return null;
      }
      resolvedDomain = clean;
    } else if (d == targetHost) {
      resolvedDomain = null; // Host-only cookie
    } else if (d.contains('.') && targetHost.endsWith('.$d')) {
      resolvedDomain = d;
    } else {
      return null;
    }
  }

  int? expiresDate;
  if (rawExpires is num) {
    final sec = rawExpires.toInt();
    final nowSec = DateTime.now().millisecondsSinceEpoch ~/ 1000;
    if (sec > nowSec && sec <= 4102444800) {
      expiresDate = sec * 1000;
    }
  }

  final sameSite = switch (rawSameSite is String ? rawSameSite.toLowerCase() : null) {
    'lax' => HTTPCookieSameSitePolicy.LAX,
    'strict' => HTTPCookieSameSitePolicy.STRICT,
    'none' => HTTPCookieSameSitePolicy.NONE,
    _ => null,
  };

  return ValidatedAppCookie(
    name: name,
    value: value,
    domain: resolvedDomain,
    path: rawPath,
    isSecure: true,
    isHttpOnly: rawHttpOnly,
    expiresDate: expiresDate,
    sameSite: sameSite,
  );
}

bool shouldReportMainFrameWebError({
  required bool? isForMainFrame,
  WebResourceErrorType? errorType,
  String? description,
}) {
  if (isForMainFrame != true) {
    return false;
  }
  if (errorType == WebResourceErrorType.CANCELLED) {
    return false;
  }
  final upper = (description ?? '').toUpperCase();
  if (upper.contains('ERR_ABORTED') || upper.contains('USER_CANCELLED')) {
    return false;
  }
  return true;
}
