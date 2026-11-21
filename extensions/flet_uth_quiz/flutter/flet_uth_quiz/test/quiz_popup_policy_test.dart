import 'package:flet_uth_quiz/src/quiz_popup_policy.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  final trustedOrigin =
      Uri.parse('https://courses.ut.edu.vn/mod/quiz/attempt.php?attempt=42');

  test('keeps Moodle blank and same-origin secure-window popups in app', () {
    for (final value in [null, '', 'about:blank']) {
      expect(
        classifyQuizPopupRequest(
          value,
          trustedOrigin,
          activePopupCount: 0,
        ),
        QuizPopupRequestKind.blank,
        reason: '$value',
      );
    }

    expect(
      classifyQuizPopupRequest(
        'https://courses.ut.edu.vn/mod/quiz/attempt.php?attempt=42&securewindow=1',
        trustedOrigin,
        activePopupCount: 0,
      ),
      QuizPopupRequestKind.sameOrigin,
    );
  });

  test('reports valid external links and rejects malformed targets', () {
    expect(
      classifyQuizPopupRequest(
        'https://example.org/help',
        trustedOrigin,
        activePopupCount: 0,
      ),
      QuizPopupRequestKind.external,
    );

    for (final value in [
      'javascript:alert(1)',
      'https://student@courses.ut.edu.vn/help',
      'https:///missing-host',
    ]) {
      expect(
        classifyQuizPopupRequest(
          value,
          trustedOrigin,
          activePopupCount: 0,
        ),
        QuizPopupRequestKind.invalid,
        reason: value,
      );
    }
  });

  test('bounds simultaneously active quiz popups', () {
    expect(
      classifyQuizPopupRequest(
        'about:blank',
        trustedOrigin,
        activePopupCount: maxQuizPopupCount - 1,
      ),
      QuizPopupRequestKind.blank,
    );
    expect(
      classifyQuizPopupRequest(
        'https://courses.ut.edu.vn/mod/quiz/attempt.php?attempt=42',
        trustedOrigin,
        activePopupCount: maxQuizPopupCount,
      ),
      QuizPopupRequestKind.limitReached,
    );
  });
}
