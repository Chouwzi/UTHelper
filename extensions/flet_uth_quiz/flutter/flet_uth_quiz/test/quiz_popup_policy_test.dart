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

  test('allows HTTPS subframes and blank frames but blocks unsafe schemes', () {
    for (final value in [
      'https://courses.ut.edu.vn/pluginfile.php/quiz-image.png',
      'https://media.example.org/quiz-video.mp4',
      'about:blank',
      'about:blank#question-frame',
    ]) {
      expect(
        allowsQuizSubframeNavigation(value),
        isTrue,
        reason: value,
      );
    }

    for (final value in [
      null,
      '',
      'http://media.example.org/insecure-video.mp4',
      'javascript:alert(1)',
      'file:///private/data',
      'content://provider/item',
      'data:text/html,example',
      'blob:https://courses.ut.edu.vn/id',
      'ftp://example.org/file',
      'https://student@media.example.org/file',
      'about:settings',
    ]) {
      expect(
        allowsQuizSubframeNavigation(value),
        isFalse,
        reason: '$value',
      );
    }
  });
}
