import 'package:flutter_inappwebview/flutter_inappwebview.dart';

final _cookieNamePattern = RegExp(r"^[!#\$%&'*+.^_`|~0-9A-Za-z-]+$");

class ValidatedQuizCookie {
  const ValidatedQuizCookie({
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

/// Validates cookies for the verified Moodle HTTPS origin.
///
/// Cookies received over the authenticated HTTPS session are installed with
/// Secure enabled even when the server omitted the Secure attribute. This
/// preserves such sessions while keeping the injected cookie HTTPS-only.
ValidatedQuizCookie validateQuizCookie(dynamic raw, Uri? trustedOrigin) {
  if (trustedOrigin == null ||
      trustedOrigin.scheme.toLowerCase() != 'https' ||
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
      secure is! bool ||
      httpOnly is! bool ||
      (sameSiteValue != null && sameSiteValue is! String) ||
      (expires != null && expires is! int)) {
    throw const FormatException();
  }

  if (domain is String && !_cookieDomainMatches(domain, trustedOrigin.host)) {
    throw const FormatException();
  }
  if (!_cookiePathApplies(cookiePath, trustedOrigin.path)) {
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

  return ValidatedQuizCookie(
    name: name,
    value: value,
    domain: domain,
    path: cookiePath,
    isSecure: true,
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

/// Removes only cookies scoped to [trustedOrigin]'s exact host so shared
/// general-WebView logins (Microsoft, Google, Portal, etc.) stay persisted.
Future<void> purgeQuizOriginCookies(
  CookieManager manager,
  Uri? trustedOrigin,
) async {
  if (trustedOrigin == null || trustedOrigin.host.isEmpty) return;
  final host = trustedOrigin.host;
  final originUri = Uri(
    scheme: trustedOrigin.scheme.isNotEmpty ? trustedOrigin.scheme : 'https',
    host: host,
    port: trustedOrigin.hasPort ? trustedOrigin.port : null,
    path: '/',
  );
  final webOrigin = WebUri.uri(originUri);

  final cookies = await manager.getCookies(url: webOrigin);
  for (final cookie in cookies) {
    final name = cookie.name;
    if (name.isEmpty) continue;
    final domain = cookie.domain;
    if (domain != null &&
        domain.isNotEmpty &&
        !_cookieDomainMatches(domain, host)) {
      continue;
    }
    try {
      await manager.deleteCookie(
        url: webOrigin,
        name: name,
        path: (cookie.path != null && cookie.path!.isNotEmpty)
            ? cookie.path!
            : '/',
        domain: domain,
      );
    } catch (_) {}
  }

  for (final targetDomain in <String>[host, '.$host']) {
    try {
      await manager.deleteCookies(
        url: webOrigin,
        domain: targetDomain,
        path: '/',
      );
    } catch (_) {}
  }
}
