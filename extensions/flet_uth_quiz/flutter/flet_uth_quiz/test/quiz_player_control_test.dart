import 'dart:io';

import 'package:flet_uth_quiz/src/quiz_player_control.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('parseQuizBaseUri enforces TrustedMoodleUri origin parity', () {
    expect(
      parseQuizBaseUri(
        'https://courses.ut.edu.vn/mod/quiz/view.php?id=42',
        requireHttps: true,
      )?.host,
      'courses.ut.edu.vn',
    );
    expect(
      parseQuizBaseUri(
        'http://moodle.example.edu/mod/quiz/view.php?id=42',
        requireHttps: false,
      )?.scheme,
      'http',
    );

    for (final invalid in [
      null,
      '',
      'https://user@courses.ut.edu.vn/mod/quiz/view.php?id=42',
      'https://courses.ut.edu.vn:443/mod/quiz/view.php?id=42',
      'https://courses.ut.edu.vn:8443/mod/quiz/view.php?id=42',
      r'https://courses.ut.edu.vn\mod/quiz/view.php?id=42',
      'https://courses.ut.edu.vn/\nmod/quiz/view.php?id=42',
      'javascript:alert(1)',
    ]) {
      expect(
        parseQuizBaseUri(invalid, requireHttps: false),
        isNull,
        reason: '$invalid',
      );
    }

    expect(
      parseQuizBaseUri(
        'http://courses.ut.edu.vn/mod/quiz/view.php?id=42',
        requireHttps: true,
      ),
      isNull,
    );
  });

  testWidgets(
      'buildQuizMainWebViewLayer uses IgnorePointer and ExcludeFocus without Offstage',
      (WidgetTester tester) async {
    const childKey = ValueKey('main-webview-child');
    Widget buildTree(bool obscured) => Directionality(
          textDirection: TextDirection.ltr,
          child: Stack(
            children: [
              buildQuizMainWebViewLayer(
                obscuredByPopup: obscured,
                child: const SizedBox(key: childKey, width: 320, height: 480),
              ),
            ],
          ),
        );

    await tester.pumpWidget(buildTree(false));
    final childFinder = find.byKey(childKey);
    expect(childFinder, findsOneWidget);
    expect(find.byType(Offstage), findsNothing);

    final ignoreWhenClear = tester.widget<IgnorePointer>(
      find.ancestor(of: childFinder, matching: find.byType(IgnorePointer)).first,
    );
    final excludeWhenClear = tester.widget<ExcludeFocus>(
      find.ancestor(of: childFinder, matching: find.byType(ExcludeFocus)).first,
    );
    expect(ignoreWhenClear.ignoring, isFalse);
    expect(excludeWhenClear.excluding, isFalse);

    await tester.pumpWidget(buildTree(true));
    expect(find.byType(Offstage), findsNothing);
    final ignoreWhenObscured = tester.widget<IgnorePointer>(
      find.ancestor(of: childFinder, matching: find.byType(IgnorePointer)).first,
    );
    final excludeWhenObscured = tester.widget<ExcludeFocus>(
      find.ancestor(of: childFinder, matching: find.byType(ExcludeFocus)).first,
    );
    expect(ignoreWhenObscured.ignoring, isTrue);
    expect(excludeWhenObscured.excluding, isTrue);
  });

  test('M3 F15 cookie persistence contract is preserved in quiz_player_control.dart', () {
    final source = File('lib/src/quiz_player_control.dart').readAsStringSync();
    expect(source.contains('clearAllCache'), isFalse);
    expect(source.contains('purgeQuizOriginCookies(_cookieManager(), _trustedOrigin)'), isTrue);
    expect(source.contains('purgeQuizOriginWebStorage('), isTrue);
    expect(source.contains('buildQuizMainWebViewLayer('), isTrue);
    expect(source.contains('runCloseCleanup('), isTrue);
    expect(source.contains('bumpSessionEpoch('), isTrue);
  });
}
