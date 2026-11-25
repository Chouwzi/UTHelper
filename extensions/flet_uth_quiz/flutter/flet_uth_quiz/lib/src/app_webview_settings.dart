import 'dart:ui' show Color;

import 'package:flutter/foundation.dart' show TargetPlatform;
import 'package:flutter_inappwebview/flutter_inappwebview.dart';

import 'quiz_popup_policy.dart' show hasUnsafeUrlCharacters;

const appWebViewMsgPrefix = '__UTH_MSG__:';
const appWebViewExternalLaunchedPrefix = '__UTH_EXTERNAL_APP_LAUNCHED__:';
const appWebViewExternalMissingPrefix = '__UTH_EXTERNAL_APP_MISSING__:';
const appWebViewMaxConsoleMsgLength = 4096;
const maxAppPopupSinks = 4;

const allowedExternalAppSchemes = <String>{
  'zalo',
  'zoomus',
  'zoomphonecall',
  'zoom',
  'msteams',
  'mailto',
  'tel',
  'sms',
  'market',
  'tg',
  'fb-messenger',
  'whatsapp',
  'viber',
  'skype',
  'slack',
  'discord',
  'googledocs',
  'googlesheets',
  'googleslides',
  'googlemeet',
  'googleclassroom',
  'googledrive',
  'vnd.youtube',
  'ms-word',
  'ms-excel',
  'ms-powerpoint',
  'ms-onenote',
  'ms-outlook',
  'onedrive',
};

const _safePublicHttpsAuthorityPattern =
    r'(?!localhost(?:[:/?#]|(?![\s\S]))|[^\x00-\x20\x7f-\x9f/@?#\\]*\.local(?:host)?(?:[:/?#]|(?![\s\S]))|127\.|10\.|192\.168\.|169\.254\.|172\.(?:1[6-9]|2[0-9]|3[01])\.|0\.|\[::1?\])[^\x00-\x20\x7f-\x9f/@?#\\]+';

/// Cancels unsafe subframe URLs natively on Android while allowing SharePoint,
/// Office Online, and Google Docs `https://`, `blob:https://`, `about:blank`,
/// and `about:srcdoc` subframes.
const appBrowserSubframeCancelRegex =
    r'(?is)^(?!(?:'
    r'(?:blob:)?https://' +
    _safePublicHttpsAuthorityPattern +
    r'(?:[/?#][^\x00-\x1f\x7f\\]*)?'
    r'|about:(?:blank|srcdoc)(?:[?#][^\x00-\x1f\x7f\\]*)?'
    r')(?![\s\S])).*';

final _cookieNamePattern = RegExp(r"^[!#\$%&'*+.^_`|~0-9A-Za-z-]+$");
final _rawAuthorityPattern = RegExp(
  r'^[a-z][a-z0-9+.-]*://([^/?#]+)',
  caseSensitive: false,
);
final _rawPathPattern = RegExp(
  r'^[a-z][a-z0-9+.-]*://[^/?#]+(/[^?#]*)',
  caseSensitive: false,
);
final _encodedTraversalPattern = RegExp(r'%(?:2e|2f|5c)', caseSensitive: false);
final _itemHexSegmentPattern = RegExp(r'^[0-9a-f]{16}$');
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
/// SharePoint Online, and Google treat the embedded browser like Chrome, while
/// omitting the `Mobile` token on tablets (`shortestSide >= 600dp`) and
/// preserving iOS Mobile Safari and desktop User-Agent strings.
String? normalizeEmbeddedUserAgent(String? rawUa, {bool isTablet = false}) {
  if (rawUa == null) return null;
  final trimmed = rawUa.trim();
  if (trimmed.isEmpty) return null;
  if (!trimmed.contains('Android')) {
    return trimmed;
  }
  var cleaned = trimmed
      .replaceAll('; wv)', ')')
      .replaceAll('; wv;', ';')
      .replaceAll(_androidWvVersionPattern, '');
  if (isTablet) {
    cleaned = cleaned.replaceAll(' Mobile Safari/', ' Safari/');
  }
  cleaned = cleaned.replaceAll(_multiSpacePattern, ' ').trim();
  return cleaned.isEmpty ? null : cleaned;
}

/// Parses `#RRGGBB`, `#AARRGGBB`, or `0xAARRGGBB` Flet color strings so the
/// cover layer over Android's hardware `SurfaceView` matches the active theme.
Color? parseAppWebViewColor(String? rawColor) {
  if (rawColor == null) return null;
  var cleaned = rawColor.trim();
  if (cleaned.isEmpty) return null;
  if (cleaned.startsWith('#')) {
    cleaned = cleaned.substring(1);
  } else if (cleaned.toLowerCase().startsWith('0x')) {
    cleaned = cleaned.substring(2);
  } else {
    return null;
  }
  if (cleaned.length == 6) {
    cleaned = 'FF$cleaned';
  }
  if (cleaned.length != 8 || !RegExp(r'^[0-9a-fA-F]{8}$').hasMatch(cleaned)) {
    return null;
  }
  final parsed = int.tryParse(cleaned, radix: 16);
  return parsed != null ? Color(parsed) : null;
}

/// Returns true once a non-blank document commits so the `#000000` hardware
/// surface cover can be lifted cleanly without a black flash, suppressing
/// spurious `onPageCommitVisible`/`onLoadStop` callbacks emitted by Chromium
/// right after cancelling an uncommitted `302` redirect (e.g. `zalo.me` -> `zalo://`).
bool shouldRevealAppWebViewSurface({
  required String? url,
  required bool hasInitialHtml,
  bool suppressAbortedRedirectCommit = false,
}) {
  if (suppressAbortedRedirectCommit) return false;
  if (hasInitialHtml) return true;
  final trimmed = (url ?? '').trim();
  if (trimmed.isEmpty) return false;
  final lowered = trimmed.toLowerCase();
  if (lowered == 'about:blank' ||
      lowered.startsWith('about:blank?') ||
      lowered.startsWith('about:blank#')) {
    return false;
  }
  return true;
}

/// Returns true when `reload()` is called before any real page has committed on
/// the controller (`null`, empty, or `about:blank`), requiring `_navigateInternal`
/// to re-dispatch [initialUrl] instead of calling no-op `controller.reload()`.
bool shouldReloadViaInitialUrl({
  required String? activeControllerUrl,
  required String initialUrl,
  required bool hasInitialHtml,
}) {
  if (hasInitialHtml || initialUrl.trim().isEmpty) {
    return false;
  }
  final active = (activeControllerUrl ?? '').trim().toLowerCase();
  return active.isEmpty ||
      active == 'about:blank' ||
      active.startsWith('about:blank?') ||
      active.startsWith('about:blank#');
}

/// Builds a Java-compatible `Pattern` for Android's `regexToCancelSubFramesLoading`
/// when `viewerLocked` is true. Cancels every subframe URL except `about:blank` and
/// (when [viewerPrefix] is present) the locked loopback viewer prefix/item or its
/// exact-origin `blob:` URLs.
String buildLockedViewerSubframeCancelRegex(String? viewerPrefix) {
  final allowed = <String>[
    r'about:blank(?:[?#][^\x00-\x1f\x7f\\]*)?',
  ];
  if (viewerPrefix != null && viewerPrefix.isNotEmpty) {
    final trimmedPrefix = viewerPrefix.trim();
    final originEnd = trimmedPrefix.indexOf('/', 'http://'.length);
    if (originEnd > 0) {
      final origin = trimmedPrefix.substring(0, originEnd + 1);
      final escapedOrigin = RegExp.escape(origin);
      allowed.add(r'blob:' + escapedOrigin + r'[^\x00-\x1f\x7f\\]*');
    }
    final escapedPrefix = RegExp.escape(trimmedPrefix);
    if (trimmedPrefix.endsWith('/')) {
      allowed.add(
        escapedPrefix +
            r'(?!(?:\.\.(?:[/?#]|(?![\s\S]))|[^?#]*(?:/\.\.(?:[/?#]|(?![\s\S]))|%2[ef]|%5c)))[^\x00-\x1f\x7f\\]*',
      );
    } else {
      allowed.add(escapedPrefix + r'(?:[?#][^\x00-\x1f\x7f\\]*)?');
    }
  }
  return r'(?is)^(?!(?:' + allowed.join('|') + r')(?![\s\S])).*';
}

InAppWebViewSettings createAppWebViewSettings({
  required bool viewerLocked,
  String? viewerPrefix,
  String? userAgent,
  bool javaScriptEnabled = true,
  bool isInspectable = false,
}) {
  return InAppWebViewSettings(
    javaScriptEnabled: javaScriptEnabled,
    domStorageEnabled: true,
    databaseEnabled: true,
    thirdPartyCookiesEnabled: !viewerLocked,
    supportMultipleWindows: !viewerLocked,
    javaScriptCanOpenWindowsAutomatically: !viewerLocked,
    useShouldOverrideUrlLoading: true,
    useOnDownloadStart: !viewerLocked,
    regexToCancelSubFramesLoading: viewerLocked
        ? buildLockedViewerSubframeCancelRegex(viewerPrefix)
        : appBrowserSubframeCancelRegex,
    useHybridComposition: true,
    hardwareAcceleration: true,
    cacheEnabled: !viewerLocked,
    cacheMode: viewerLocked ? CacheMode.LOAD_NO_CACHE : CacheMode.LOAD_DEFAULT,
    incognito: false,
    saveFormData: false,
    isInspectable: isInspectable,
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
  final trimmed = rawUrl.trim();
  if (_authorityHasUserInfo(trimmed)) {
    return null;
  }
  final uri = Uri.tryParse(trimmed);
  if (uri == null ||
      uri.scheme.toLowerCase() != 'http' ||
      uri.userInfo.isNotEmpty ||
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
  if (segs.length == 1 &&
      !uri.path.endsWith('/') &&
      _itemHexSegmentPattern.hasMatch(segs[0])) {
    return 'http://$host:${uri.port}/${segs[0]}';
  }
  return null;
}

bool _authorityHasUserInfo(String rawUrl) {
  final rawAuthority = _rawAuthorityPattern.firstMatch(rawUrl)?.group(1);
  if (rawAuthority == null || rawAuthority.isEmpty) return true;
  return rawAuthority.contains('@');
}

bool _rawPathHasEncodedTraversal(String rawUrl) {
  final rawPath = _rawPathPattern.firstMatch(rawUrl)?.group(1);
  if (rawPath == null || rawPath.isEmpty) return false;
  return _encodedTraversalPattern.hasMatch(rawPath);
}

bool _isUnsafeIpv4Octets(int a, int b, int c, int d) {
  if (a == 0 || a == 10 || a == 127 || a >= 224) return true;
  if (a == 100 && b >= 64 && b <= 127) return true;
  if (a == 169 && b == 254) return true;
  if (a == 172 && b >= 16 && b <= 31) return true;
  if (a == 192 && b == 168) return true;
  if (a == 192 && b == 0 && (c == 0 || c == 2)) return true;
  if (a == 198 && (b == 18 || b == 19)) return true;
  if (a == 198 && b == 51 && c == 100) return true;
  if (a == 203 && b == 0 && c == 113) return true;
  return false;
}

int? _parseIpv4Int(String host) {
  if (host.isEmpty || host.startsWith('.') || host.endsWith('.')) return null;
  final parts = host.split('.');
  if (parts.isEmpty || parts.length > 4) return null;
  final values = <int>[];
  for (final part in parts) {
    if (part.isEmpty) return null;
    final lower = part.toLowerCase();
    int? parsed;
    if (lower.startsWith('0x')) {
      final digits = lower.substring(2);
      if (digits.isEmpty || !RegExp(r'^[0-9a-f]+$').hasMatch(digits)) {
        return null;
      }
      parsed = int.tryParse(digits, radix: 16);
    } else if (lower.length > 1 && lower.startsWith('0')) {
      if (!RegExp(r'^[0-7]+$').hasMatch(lower)) return null;
      parsed = int.tryParse(lower, radix: 8);
    } else {
      if (!RegExp(r'^[0-9]+$').hasMatch(lower)) return null;
      parsed = int.tryParse(lower, radix: 10);
    }
    if (parsed == null || parsed < 0) return null;
    values.add(parsed);
  }
  if (values.length == 1) {
    if (values[0] > 0xFFFFFFFF) return null;
    return values[0];
  }
  if (values.length == 2) {
    if (values[0] > 0xFF || values[1] > 0xFFFFFF) return null;
    return (values[0] << 24) | values[1];
  }
  if (values.length == 3) {
    if (values[0] > 0xFF || values[1] > 0xFF || values[2] > 0xFFFF) return null;
    return (values[0] << 24) | (values[1] << 16) | values[2];
  }
  if (values.any((v) => v > 0xFF)) return null;
  return (values[0] << 24) | (values[1] << 16) | (values[2] << 8) | values[3];
}

List<int>? _parseIpv6Bytes(String raw) {
  var addr = raw.trim();
  if (addr.startsWith('[') && addr.endsWith(']')) {
    addr = addr.substring(1, addr.length - 1);
  }
  final zoneIdx = addr.indexOf('%');
  if (zoneIdx >= 0) {
    addr = addr.substring(0, zoneIdx);
  }
  if (addr.isEmpty) return null;

  // Handle trailing dotted IPv4 segment (e.g. ::ffff:127.0.0.1)
  final lastColon = addr.lastIndexOf(':');
  if (lastColon >= 0 && addr.substring(lastColon + 1).contains('.')) {
    final ipv4 = _parseIpv4Int(addr.substring(lastColon + 1));
    if (ipv4 == null) return null;
    final hi = (ipv4 >> 16) & 0xFFFF;
    final lo = ipv4 & 0xFFFF;
    addr =
        '${addr.substring(0, lastColon + 1)}${hi.toRadixString(16)}:${lo.toRadixString(16)}';
  }

  final doubleColonParts = addr.split('::');
  if (doubleColonParts.length > 2) return null;

  List<int>? parseGroups(String s) {
    if (s.isEmpty) return <int>[];
    final out = <int>[];
    for (final g in s.split(':')) {
      if (g.isEmpty || g.length > 4 || !RegExp(r'^[0-9a-fA-F]+$').hasMatch(g)) {
        return null;
      }
      final val = int.tryParse(g, radix: 16);
      if (val == null || val < 0 || val > 0xFFFF) return null;
      out.add(val);
    }
    return out;
  }

  final groups = <int>[];
  if (doubleColonParts.length == 2) {
    final left = parseGroups(doubleColonParts[0]);
    final right = parseGroups(doubleColonParts[1]);
    if (left == null || right == null) return null;
    final missing = 8 - (left.length + right.length);
    if (missing < 1) return null;
    groups
      ..addAll(left)
      ..addAll(List<int>.filled(missing, 0))
      ..addAll(right);
  } else {
    final full = parseGroups(addr);
    if (full == null || full.length != 8) return null;
    groups.addAll(full);
  }

  final bytes = <int>[];
  for (final g in groups) {
    bytes
      ..add((g >> 8) & 0xFF)
      ..add(g & 0xFF);
  }
  return bytes;
}

bool _isUnsafeHttpsHost(String rawHost) {
  var host = rawHost.trim().toLowerCase();
  if (host.startsWith('[') && host.endsWith(']')) {
    host = host.substring(1, host.length - 1);
  }
  while (host.endsWith('.')) {
    host = host.substring(0, host.length - 1);
  }
  if (host.isEmpty ||
      host == 'localhost' ||
      host.endsWith('.localhost') ||
      host.endsWith('.local')) {
    return true;
  }
  if (host.contains(':')) {
    final bytes = _parseIpv6Bytes(host);
    if (bytes == null) return true;
    final isV4Mapped = bytes.sublist(0, 10).every((b) => b == 0) &&
        bytes[10] == 0xFF &&
        bytes[11] == 0xFF;
    if (isV4Mapped) {
      return _isUnsafeIpv4Octets(bytes[12], bytes[13], bytes[14], bytes[15]);
    }
    final isAllZeroPrefix12 = bytes.sublist(0, 12).every((b) => b == 0);
    if (isAllZeroPrefix12) return true; // ::, ::1, deprecated IPv4-compat
    if (bytes[0] == 0xFE && (bytes[1] & 0xC0) == 0x80) return true; // fe80::/10
    if (bytes[0] == 0xFE && (bytes[1] & 0xC0) == 0xC0) return true; // fec0::/10
    if ((bytes[0] & 0xFE) == 0xFC) return true; // fc00::/7 ULA
    if (bytes[0] == 0xFF) return true; // ff00::/8 multicast
    if (bytes[0] == 0x20 &&
        bytes[1] == 0x01 &&
        bytes[2] == 0x0D &&
        bytes[3] == 0xB8) {
      return true; // 2001:db8::/32 documentation
    }
    if ((bytes[0] & 0xE0) != 0x20) return true; // non-global unicast
    return false;
  }
  final ipv4 = _parseIpv4Int(host);
  if (ipv4 != null) {
    return _isUnsafeIpv4Octets(
      (ipv4 >> 24) & 0xFF,
      (ipv4 >> 16) & 0xFF,
      (ipv4 >> 8) & 0xFF,
      ipv4 & 0xFF,
    );
  }
  return false;
}

bool _isValidHttpsBrowserUrl(String rawUrl) {
  if (rawUrl.isEmpty ||
      hasUnsafeUrlCharacters(rawUrl) ||
      rawUrl.contains(RegExp(r'\s')) ||
      _authorityHasUserInfo(rawUrl)) {
    return false;
  }
  final uri = Uri.tryParse(rawUrl);
  // Note: Dart's `Uri.isAbsolute` is `hasScheme && !hasFragment` (RFC 3986
  // absolute-URI), which would falsely reject valid `#gid=...` / SPA `#` URLs!
  if (uri == null ||
      !uri.hasScheme ||
      !uri.hasAuthority ||
      uri.scheme.toLowerCase() != 'https' ||
      uri.host.isEmpty ||
      uri.userInfo.isNotEmpty ||
      _isUnsafeHttpsHost(uri.host)) {
    return false;
  }
  return true;
}

class ExternalAppNavigationPlan {
  const ExternalAppNavigationPlan({
    required this.launchUri,
    required this.scheme,
    this.httpsFallbackUrl,
    this.marketFallbackUri,
    this.isStoreFallback = false,
  });

  final String launchUri;
  final String scheme;
  final String? httpsFallbackUrl;
  final String? marketFallbackUri;
  final bool isStoreFallback;
}

bool isSameNavigationUrlIgnoringFragment(String firstUrl, String secondUrl) {
  final firstTrimmed = firstUrl.trim();
  final secondTrimmed = secondUrl.trim();
  if (firstTrimmed.isEmpty || secondTrimmed.isEmpty) return false;
  final a = Uri.tryParse(firstTrimmed);
  final b = Uri.tryParse(secondTrimmed);
  if (a == null || b == null || !a.hasScheme || !b.hasScheme) {
    return firstTrimmed == secondTrimmed;
  }
  final schemeA = a.scheme.toLowerCase();
  final schemeB = b.scheme.toLowerCase();
  if (schemeA != schemeB) return false;
  if (a.host.toLowerCase() != b.host.toLowerCase()) return false;
  final portA = a.hasPort ? a.port : (schemeA == 'https' ? 443 : 80);
  final portB = b.hasPort ? b.port : (schemeB == 'https' ? 443 : 80);
  if (portA != portB) return false;
  final pathA = a.path.isEmpty ? '/' : a.path;
  final pathB = b.path.isEmpty ? '/' : b.path;
  return pathA == pathB && a.query == b.query;
}

bool _isPlayStoreHttpsUrl(String url) {
  final uri = Uri.tryParse(url.trim());
  if (uri == null) return false;
  final host = uri.host.toLowerCase();
  return host == 'play.google.com' && uri.path.startsWith('/store/apps/details');
}

/// Returns true when [rawUrl] targets a UTH LMS/Portal domain (`ut.edu.vn` or
/// `*.ut.edu.vn`), used to enforce Demo Mode offline isolation in WebViews.
bool isUthLmsBrowserUrl(String? rawUrl) {
  if (rawUrl == null) return false;
  final trimmed = rawUrl.trim();
  if (trimmed.isEmpty) return false;
  final uri = Uri.tryParse(trimmed);
  if (uri == null || !uri.hasAuthority || uri.host.isEmpty) return false;
  var host = uri.host.toLowerCase();
  while (host.endsWith('.')) {
    host = host.substring(0, host.length - 1);
  }
  return host == 'ut.edu.vn' || host.endsWith('.ut.edu.vn');
}

/// Returns true for empty or initial `about:blank` URLs emitted by Android's
/// child `WebViewTransport` before the real `window.open` target URL commits.
bool isTransientPopupSinkBootstrapUrl(String? rawUrl) {
  if (rawUrl == null) return true;
  final lowered = rawUrl.trim().toLowerCase();
  return lowered.isEmpty ||
      lowered == 'about:blank' ||
      lowered.startsWith('about:blank?') ||
      lowered.startsWith('about:blank#');
}

bool _isSafeMarketFallbackUri(String raw) {
  final trimmed = raw.trim();
  if (trimmed.isEmpty ||
      hasUnsafeUrlCharacters(trimmed) ||
      trimmed.contains(RegExp(r'\s'))) {
    return false;
  }
  return trimmed.toLowerCase().startsWith('market://details');
}

/// Extracts `S.browser_fallback_url` from Android `intent://...#Intent;...;end`
/// redirects emitted by SharePoint, OneDrive, Google Drive, or Zalo, or
/// synthesizes `https://<host><path>` when `scheme=https` is declared and
/// `S.browser_fallback_url` is absent or points to `market://details?...`.
String? resolveAndroidIntentFallbackUrl(
  String? rawUrl, {
  String? currentUrl,
}) {
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
  String? declaredScheme;
  String? rawFallbackEncoded;
  for (final rawPart in tail.split(';')) {
    if (rawPart.startsWith('S.browser_fallback_url=')) {
      rawFallbackEncoded = rawPart.substring('S.browser_fallback_url='.length);
    } else if (rawPart.toLowerCase().startsWith('scheme=')) {
      declaredScheme = rawPart.substring('scheme='.length).trim().toLowerCase();
    }
  }

  if (rawFallbackEncoded != null) {
    if (rawFallbackEncoded.isEmpty) return null;
    String decoded;
    try {
      decoded = Uri.decodeComponent(rawFallbackEncoded).trim();
    } catch (_) {
      return null;
    }
    if (_isValidHttpsBrowserUrl(decoded)) {
      if (currentUrl != null &&
          isSameNavigationUrlIgnoringFragment(decoded, currentUrl)) {
        return null;
      }
      return decoded;
    }
    // Allow non-browser Play Store `market://details...` fallbacks to fall
    // through to `scheme=https` synthesis while still rejecting explicit unsafe
    // browser fallbacks (`javascript:`, `http://`, SSRF, etc.).
    if (!_isSafeMarketFallbackUri(decoded)) {
      return null;
    }
  }

  if (declaredScheme == 'https') {
    final hostAndPath = trimmed.substring('intent://'.length, markerIndex);
    if (hostAndPath.isEmpty) return null;
    final candidate = 'https://$hostAndPath';
    if (_isValidHttpsBrowserUrl(candidate)) {
      if (currentUrl != null &&
          isSameNavigationUrlIgnoringFragment(candidate, currentUrl)) {
        return null;
      }
      return candidate;
    }
  }
  return null;
}

/// Plans native external app handoff for main-frame `intent://` and allowlisted
/// custom schemes (`zalo://`, `zoomus://`, `msteams://`, `mailto:`, `tel:`, etc.)
/// when [viewerLocked] is false.
ExternalAppNavigationPlan? resolveExternalAppNavigation(
  String? rawUrl, {
  required bool isForMainFrame,
  required bool viewerLocked,
  String? currentUrl,
  bool blockUthLms = false,
  TargetPlatform platform = TargetPlatform.android,
}) {
  if (viewerLocked || !isForMainFrame || rawUrl == null) {
    return null;
  }
  final trimmed = rawUrl.trim();
  if (trimmed.isEmpty ||
      hasUnsafeUrlCharacters(trimmed) ||
      trimmed.contains(RegExp(r'\s'))) {
    return null;
  }
  final lowered = trimmed.toLowerCase();
  if (lowered.startsWith('intent://')) {
    if (platform != TargetPlatform.android) return null;
    final markerIndex = lowered.indexOf('#intent;');
    if (markerIndex < 0) return null;
    final hostAndPath = trimmed.substring('intent://'.length, markerIndex);
    String? declaredScheme;
    String? marketUri;
    final tail = trimmed.substring(markerIndex + '#Intent;'.length);
    for (final rawPart in tail.split(';')) {
      if (rawPart.startsWith('S.browser_fallback_url=')) {
        final encoded = rawPart.substring('S.browser_fallback_url='.length);
        if (encoded.isNotEmpty) {
          try {
            final decoded = Uri.decodeComponent(encoded).trim();
            if (_isSafeMarketFallbackUri(decoded)) {
              marketUri = decoded;
            }
          } catch (_) {}
        }
      } else if (rawPart.toLowerCase().startsWith('scheme=')) {
        declaredScheme =
            rawPart.substring('scheme='.length).trim().toLowerCase();
      }
    }
    if (declaredScheme == null ||
        declaredScheme.isEmpty ||
        (declaredScheme != 'https' &&
            !allowedExternalAppSchemes.contains(declaredScheme))) {
      return null;
    }
    if (declaredScheme == 'https') {
      final candidateHttps = 'https://$hostAndPath';
      if (hostAndPath.isEmpty || !_isValidHttpsBrowserUrl(candidateHttps)) {
        return null;
      }
      if (blockUthLms && isUthLmsBrowserUrl(candidateHttps)) {
        return null;
      }
    }
    final httpsFallback = resolveAndroidIntentFallbackUrl(
      trimmed,
      currentUrl: currentUrl,
    );
    if (blockUthLms &&
        httpsFallback != null &&
        isUthLmsBrowserUrl(httpsFallback)) {
      return null;
    }
    final storeFallback =
        httpsFallback != null && _isPlayStoreHttpsUrl(httpsFallback);
    return ExternalAppNavigationPlan(
      launchUri: trimmed,
      scheme: 'intent',
      httpsFallbackUrl: httpsFallback,
      marketFallbackUri: marketUri,
      isStoreFallback: storeFallback,
    );
  }

  final schemeMatch = RegExp(r'^([a-z][a-z0-9+.-]*):').firstMatch(lowered);
  if (schemeMatch == null) return null;
  final scheme = schemeMatch.group(1)!;
  if (!allowedExternalAppSchemes.contains(scheme)) {
    return null;
  }
  return ExternalAppNavigationPlan(
    launchUri: trimmed,
    scheme: scheme,
    isStoreFallback: scheme == 'market',
  );
}

bool _isSafeAboutDocumentUrl(String lowered, {required bool viewerLocked}) {
  if (lowered == 'about:blank' ||
      lowered.startsWith('about:blank?') ||
      lowered.startsWith('about:blank#')) {
    return true;
  }
  if (!viewerLocked &&
      (lowered == 'about:srcdoc' ||
          lowered.startsWith('about:srcdoc?') ||
          lowered.startsWith('about:srcdoc#'))) {
    return true;
  }
  return false;
}

AppNavigationDecision decideAppWebViewNavigation(
  String? rawUrl, {
  required bool isForMainFrame,
  required bool viewerLocked,
  String? viewerPrefix,
  Set<int> allowedLoopbackPorts = const <int>{},
  bool blockUthLms = false,
}) {
  if (rawUrl == null) return const AppNavigationDecision.cancel();
  final trimmed = rawUrl.trim();
  if (trimmed.isEmpty ||
      hasUnsafeUrlCharacters(trimmed) ||
      trimmed.contains(RegExp(r'\s'))) {
    return const AppNavigationDecision.cancel();
  }

  final lowered = trimmed.toLowerCase();
  if (_isSafeAboutDocumentUrl(lowered, viewerLocked: viewerLocked)) {
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
    if (_isValidHttpsBrowserUrl(inner)) {
      if (blockUthLms && isUthLmsBrowserUrl(inner)) {
        return const AppNavigationDecision.cancel();
      }
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
    if (_authorityHasUserInfo(trimmed) || _rawPathHasEncodedTraversal(trimmed)) {
      return const AppNavigationDecision.cancel();
    }
    final uri = Uri.tryParse(trimmed);
    if (uri == null ||
        !uri.hasScheme ||
        !uri.hasAuthority ||
        !uri.hasPort ||
        uri.port < 1 ||
        uri.port > 65535 ||
        uri.userInfo.isNotEmpty ||
        uri.path.split('/').contains('..')) {
      return const AppNavigationDecision.cancel();
    }
    final prefixTrimmed = viewerPrefix.trim();
    final prefixUri = Uri.tryParse(prefixTrimmed);
    if (prefixUri == null ||
        !prefixUri.hasScheme ||
        !prefixUri.hasAuthority ||
        !prefixUri.hasPort) {
      return const AppNavigationDecision.cancel();
    }
    final normalized =
        '${uri.scheme.toLowerCase()}://${uri.host.toLowerCase()}:${uri.port}${uri.path}';
    final normalizedPrefix =
        '${prefixUri.scheme.toLowerCase()}://${prefixUri.host.toLowerCase()}:${prefixUri.port}${prefixUri.path}';
    final matched = normalizedPrefix.endsWith('/')
        ? normalized.startsWith(normalizedPrefix)
        : normalized == normalizedPrefix;
    return matched
        ? const AppNavigationDecision.allow()
        : const AppNavigationDecision.cancel();
  }

  if (lowered.startsWith('intent://')) {
    if (isForMainFrame) {
      final fallback = resolveAndroidIntentFallbackUrl(trimmed);
      if (fallback != null) {
        if (blockUthLms && isUthLmsBrowserUrl(fallback)) {
          return const AppNavigationDecision.cancel();
        }
        return AppNavigationDecision.redirectInPlace(fallback);
      }
    }
    return const AppNavigationDecision.cancel();
  }

  if (_isValidHttpsBrowserUrl(trimmed)) {
    if (blockUthLms && isUthLmsBrowserUrl(trimmed)) {
      return const AppNavigationDecision.cancel();
    }
    return const AppNavigationDecision.allow();
  }

  final uri = Uri.tryParse(trimmed);
  if (uri != null &&
      uri.hasScheme &&
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
      value.contains(RegExp(r'[\x00-\x20\x7f-\x9f;,]')) ||
      (rawDomain != null && rawDomain is! String) ||
      rawPath is! String ||
      !rawPath.startsWith('/') ||
      rawPath.contains(RegExp(r'[\x00-\x20\x7f-\x9f;]')) ||
      rawSecure is! bool ||
      rawHttpOnly is! bool) {
    return null;
  }

  final targetHost = targetUri.host.toLowerCase();
  if (rawDomain is String && rawDomain.trim().isNotEmpty) {
    var d = rawDomain.trim().toLowerCase();
    if (d.endsWith('.local')) {
      d = d.substring(0, d.length - '.local'.length);
    }
    if (d.startsWith('.')) {
      d = d.substring(1);
    }
    if (d.isEmpty || d.startsWith('.') || d != targetHost) {
      return null;
    }
  }
  const String? resolvedDomain = null; // Host-only cookie matching quiz_cookie_policy.dart

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

/// Resolves an in-place main-frame navigation target for `onCreateWindow`,
/// returning `null` for `viewerLocked`, cancelled URLs, `about:blank`, and `blob:`.
String? resolveCreateWindowInPlaceTarget(
  String? rawUrl, {
  required bool viewerLocked,
  Set<int> allowedLoopbackPorts = const <int>{},
  bool blockUthLms = false,
}) {
  if (viewerLocked || rawUrl == null || rawUrl.trim().isEmpty) {
    return null;
  }
  final decision = decideAppWebViewNavigation(
    rawUrl,
    isForMainFrame: true,
    viewerLocked: false,
    allowedLoopbackPorts: allowedLoopbackPorts,
    blockUthLms: blockUthLms,
  );
  final target = switch (decision.kind) {
    AppNavigationKind.allow => rawUrl.trim(),
    AppNavigationKind.redirectInPlace => decision.targetUrl,
    AppNavigationKind.cancel => null,
  };
  if (target == null || target.isEmpty) return null;
  final lowered = target.toLowerCase();
  if (_isSafeAboutDocumentUrl(lowered, viewerLocked: false) ||
      lowered.startsWith('blob:')) {
    return null;
  }
  return target;
}

/// Validates a file download URL emitted by `onDownloadStartRequest` for
/// delegation to `external_url`, allowing only public `https://` URLs when
/// [viewerLocked] is false.
String? resolveDownloadExternalUrl(
  String? rawUrl, {
  required bool viewerLocked,
  bool blockUthLms = false,
}) {
  if (viewerLocked || rawUrl == null) return null;
  final trimmed = rawUrl.trim();
  if (!_isValidHttpsBrowserUrl(trimmed)) return null;
  if (blockUthLms && isUthLmsBrowserUrl(trimmed)) return null;
  return trimmed;
}

/// Determines whether a `didUpdateWidget` / property sync should navigate the
/// live `InAppWebViewController`, suppressing echoes of the already-loaded URL.
bool shouldNavigateOnWidgetUrlSync({
  required String nextUrl,
  required String previousInitialUrl,
  required String currentLoadedUrl,
}) {
  final trimmed = nextUrl.trim();
  if (trimmed.isEmpty) return false;
  if (trimmed == previousInitialUrl.trim()) return false;
  if (trimmed == currentLoadedUrl.trim()) return false;
  return true;
}

/// Forwards `__UTH_MSG__:` console bridge events only in locked document viewers
/// and within the strict payload length cap.
bool shouldForwardAppConsoleMessage(
  String msg, {
  required bool viewerLocked,
}) {
  return viewerLocked &&
      msg.startsWith(appWebViewMsgPrefix) &&
      msg.length <= appWebViewMaxConsoleMsgLength;
}

/// Returns true on platforms where `onCreateWindow` child `windowId` must be
/// attached to an offstage `InAppWebView` sink (Android needs `WebViewTransport`
/// to receive JS `window.open(url)` targets and release `resultMsg`; iOS and
/// Windows otherwise fall back to navigating the main frame to blocked URLs).
bool shouldAdoptAppPopupWindowSink(TargetPlatform platform) =>
    platform == TargetPlatform.android ||
    platform == TargetPlatform.iOS ||
    platform == TargetPlatform.windows;

/// Returns true when cookies installed after an initial timeout navigation should
/// trigger a single reload of [_initialUrl], skipping reload if the WebView has
/// already redirected to a different host (e.g. SharePoint / Microsoft SSO).
bool shouldReloadAfterLateCookies({
  required int appliedCount,
  required bool initialNavDispatched,
  required bool alreadyReloaded,
  required bool disposed,
  required bool hasController,
  required String initialUrl,
  String currentLoadedUrl = '',
}) {
  if (appliedCount <= 0 ||
      !initialNavDispatched ||
      alreadyReloaded ||
      disposed ||
      !hasController ||
      initialUrl.isEmpty) {
    return false;
  }
  final trimmedCurrent = currentLoadedUrl.trim();
  if (trimmedCurrent.isNotEmpty) {
    final initUri = Uri.tryParse(initialUrl.trim());
    final curUri = Uri.tryParse(trimmedCurrent);
    if (initUri == null ||
        curUri == null ||
        initUri.host.isEmpty ||
        curUri.host.toLowerCase() != initUri.host.toLowerCase()) {
      return false;
    }
  }
  return true;
}

/// Evicts oldest popup sinks so adding a new entry never exceeds [maxCount].
void trimPopupSinksToCap<T>(
  List<T> sinks, {
  required int maxCount,
  required void Function(T evicted) onEvict,
}) {
  while (sinks.length >= maxCount && sinks.isNotEmpty) {
    final oldest = sinks.removeAt(0);
    onEvict(oldest);
  }
}

/// Returns true when Android `shouldOverrideUrlLoading` should issue a fresh
/// headerless `controller.loadUrl(...)` and return `CANCEL` instead of `ALLOW`.
///
/// On Android, `InAppWebViewClientCompat.allowShouldOverrideUrlLoading` calls
/// `webView.loadUrl(url, request.getRequestHeaders())`, forwarding the previous
/// origin's HTTP/2 request headers (`Sec-Fetch-*`, `Origin`, etc.) as custom
/// extra headers across redirects (`login.microsoftonline.com` -> `office.com`,
/// `sharepoint.com` -> `officeapps.live.com`), causing strict HTTP/2 edges to
/// abort the stream with `net::ERR_HTTP2_PROTOCOL_ERROR`.
bool shouldUseCleanAndroidMainFrameLoad({
  required TargetPlatform platform,
  required bool viewerLocked,
  required bool isForMainFrame,
  required AppNavigationKind decisionKind,
  required String? rawUrl,
  required String? method,
  String? currentUrl,
}) {
  if (platform != TargetPlatform.android ||
      viewerLocked ||
      !isForMainFrame ||
      decisionKind != AppNavigationKind.allow ||
      rawUrl == null) {
    return false;
  }
  final normalizedMethod = (method ?? 'GET').trim().toUpperCase();
  if (normalizedMethod.isNotEmpty && normalizedMethod != 'GET') {
    return false;
  }
  final trimmedUrl = rawUrl.trim();
  if (!trimmedUrl.toLowerCase().startsWith('https://')) {
    return false;
  }
  final targetUri = Uri.tryParse(trimmedUrl);
  if (targetUri == null || targetUri.host.isEmpty) {
    return false;
  }
  final trimmedCurrent = (currentUrl ?? '').trim();
  if (trimmedCurrent.isEmpty) {
    return false;
  }
  final currentUri = Uri.tryParse(trimmedCurrent);
  if (currentUri == null || currentUri.host.isEmpty) {
    return false;
  }
  return currentUri.host.toLowerCase() != targetUri.host.toLowerCase();
}

/// Returns true when an external-app navigation intercepted in
/// `shouldOverrideUrlLoading` occurred as a server-side HTTP redirect before
/// any HTML document committed in the main frame (for example
/// `https://zalo.me/... -> 302 -> zalo://...` or
/// `https://meet.google.com/... -> 302 -> intent://...`).
///
/// Returns false when a `200 OK` HTML page has already started/committed in the
/// main frame and probes a custom scheme via inline JavaScript (for example
/// `https://zoom.us/j/...` running `window.location = "zoomus://..."`), so the
/// live web page remains interactive when the native app is not installed.
bool isUncommittedPreDocumentExternalRedirect({
  required bool hasCommittedPage,
  required bool hasDocumentTitle,
  required bool? isRedirect,
}) {
  if (hasCommittedPage || hasDocumentTitle) return false;
  return isRedirect == true;
}
