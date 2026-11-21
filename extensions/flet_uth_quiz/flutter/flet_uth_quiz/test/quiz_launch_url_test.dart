import 'package:flet_uth_quiz/src/quiz_player_control.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  final trustedOrigin =
      Uri.parse('https://courses.ut.edu.vn/mod/quiz/view.php?id=42');

  test('accepts a server-returned attempt URL with display state', () {
    final url = validateQuizLaunchUrl(
      'https://courses.ut.edu.vn/mod/quiz/attempt.php'
      '?attempt=918&page=2&cmid=42&display=compact#question-7',
      trustedOrigin,
    );

    expect(url?.path, '/mod/quiz/attempt.php');
    expect(url?.queryParameters['attempt'], '918');
    expect(url?.fragment, 'question-7');
  });

  test('accepts a server-returned overdue summary URL', () {
    final url = validateQuizLaunchUrl(
      'https://courses.ut.edu.vn/mod/quiz/summary.php'
      '?attempt=918&display=review',
      trustedOrigin,
    );

    expect(url?.path, '/mod/quiz/summary.php');
    expect(url?.queryParameters['attempt'], '918');
  });

  test('rejects a foreign origin', () {
    expect(
      validateQuizLaunchUrl(
        'https://thnn.ut.edu.vn/mod/quiz/attempt.php?attempt=918',
        trustedOrigin,
      ),
      isNull,
    );
  });

  test('rejects credentials, ports, and routes outside the attempt flow', () {
    for (final value in [
      'https://student@courses.ut.edu.vn/mod/quiz/attempt.php?attempt=918',
      'https://courses.ut.edu.vn:443/mod/quiz/attempt.php?attempt=918',
      'http://courses.ut.edu.vn/mod/quiz/attempt.php?attempt=918',
      'https://courses.ut.edu.vn/mod/quiz/startattempt.php?attempt=918',
      'https://courses.ut.edu.vn/mod/quiz/processattempt.php?attempt=918',
      'https://courses.ut.edu.vn/mod/quiz/view.php?id=42',
      'https://courses.ut.edu.vn/course/view.php?id=42',
      'https://courses.ut.edu.vn/login/logout.php?attempt=918',
      'https://courses.ut.edu.vn/mod/quiz/attempt.php?attempt=918&action=finish',
    ]) {
      expect(validateQuizLaunchUrl(value, trustedOrigin), isNull,
          reason: value);
    }
  });

  test('requires exactly one positive server attempt ID', () {
    for (final value in [
      'https://courses.ut.edu.vn/mod/quiz/attempt.php',
      'https://courses.ut.edu.vn/mod/quiz/attempt.php?attempt=0',
      'https://courses.ut.edu.vn/mod/quiz/attempt.php?attempt=-1',
      'https://courses.ut.edu.vn/mod/quiz/attempt.php?attempt=9&attempt=10',
    ]) {
      expect(validateQuizLaunchUrl(value, trustedOrigin), isNull,
          reason: value);
    }
  });
}
