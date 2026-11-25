import 'package:flet_uth_quiz/src/quiz_cookie_policy.dart';
import 'package:flutter_inappwebview/flutter_inappwebview.dart';
import 'package:flutter_test/flutter_test.dart';

class _RecordingCookieManager implements CookieManager {
  _RecordingCookieManager(this.cookies);

  final List<Cookie> cookies;
  int deleteAllCalls = 0;
  final List<String> deletedNames = <String>[];
  final List<String?> deletedDomains = <String?>[];

  @override
  Future<List<Cookie>> getCookies({
    required WebUri url,
    InAppWebViewController? iosBelow11WebViewController,
    InAppWebViewController? webViewController,
  }) async =>
      cookies;

  @override
  Future<bool> deleteCookie({
    required WebUri url,
    required String name,
    String path = '/',
    String? domain,
    InAppWebViewController? iosBelow11WebViewController,
    InAppWebViewController? webViewController,
  }) async {
    deletedNames.add(name);
    return true;
  }

  @override
  Future<bool> deleteCookies({
    required WebUri url,
    String path = '/',
    String? domain,
    InAppWebViewController? iosBelow11WebViewController,
    InAppWebViewController? webViewController,
  }) async {
    deletedDomains.add(domain);
    return true;
  }

  @override
  Future<bool> deleteAllCookies() async {
    deleteAllCalls++;
    return true;
  }

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

void main() {
  final trustedOrigin =
      Uri.parse('https://courses.ut.edu.vn/mod/quiz/attempt.php?attempt=42');

  Map<String, Object?> cookie({
    Object? secure = true,
    Object? domain,
  }) =>
      {
        'name': 'MoodleSession',
        'value': 'test-cookie-value',
        'domain': domain,
        'path': '/',
        'secure': secure,
        'http_only': true,
        'same_site': 'Lax',
        'expires': null,
      };

  test('accepts server cookies with Secure omitted and keeps them HTTPS-only',
      () {
    final validated = validateQuizCookie(
      cookie(secure: false),
      trustedOrigin,
    );

    expect(validated.name, 'MoodleSession');
    expect(validated.isSecure, isTrue);
    expect(validated.isHttpOnly, isTrue);
    expect(validated.sameSite, HTTPCookieSameSitePolicy.LAX);
  });

  test('preserves Secure cookies and allows host-only cookies', () {
    final validated = validateQuizCookie(cookie(), trustedOrigin);

    expect(validated.isSecure, isTrue);
    expect(validated.domain, isNull);
  });

  test('rejects non-HTTPS origins, foreign domains, and invalid flags', () {
    expect(
      () => validateQuizCookie(
        cookie(),
        Uri.parse('http://courses.ut.edu.vn/mod/quiz/attempt.php'),
      ),
      throwsFormatException,
    );
    expect(
      () => validateQuizCookie(
        cookie(domain: 'example.org'),
        trustedOrigin,
      ),
      throwsFormatException,
    );
    expect(
      () => validateQuizCookie(
        cookie(secure: 'false'),
        trustedOrigin,
      ),
      throwsFormatException,
    );
  });

  test('purgeQuizOriginCookies deletes only trustedOrigin cookies and preserves Microsoft/Google/Portal',
      () async {
    final manager = _RecordingCookieManager(<Cookie>[
      Cookie(name: 'MoodleSession', value: 'm1', domain: 'courses.ut.edu.vn', path: '/'),
      Cookie(name: 'MOODLEID1_', value: 'm2', domain: null, path: '/'),
      Cookie(name: 'ESTSAUTH', value: 'ms1', domain: '.login.microsoftonline.com', path: '/'),
      Cookie(name: 'PortalToken', value: 'p1', domain: '.ut.edu.vn', path: '/'),
    ]);

    await purgeQuizOriginCookies(manager, trustedOrigin);

    expect(manager.deleteAllCalls, 0);
    expect(manager.deletedNames, <String>['MoodleSession', 'MOODLEID1_']);
    expect(manager.deletedDomains, <String>['courses.ut.edu.vn', '.courses.ut.edu.vn']);
  });
}
