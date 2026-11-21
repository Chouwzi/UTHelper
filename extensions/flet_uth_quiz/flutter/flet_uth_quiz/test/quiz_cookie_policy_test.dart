import 'package:flet_uth_quiz/src/quiz_cookie_policy.dart';
import 'package:flutter_inappwebview/flutter_inappwebview.dart';
import 'package:flutter_test/flutter_test.dart';

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
}
