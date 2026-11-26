import 'dart:io';

import 'package:flet_uth_quiz/src/quiz_player_control.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_inappwebview/flutter_inappwebview.dart';
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

  test('MOB-WEBVIEW-01: every QuizPlayer InAppWebView mount site defines non-null onRenderProcessGone and surfaces clean error without auto-reloading', () {
    final source = File('lib/src/quiz_player_control.dart').readAsStringSync();
    final mountArgs = _extractQuizInAppWebViewCalls(source);
    // 3 mount sites: _blockedPopupSink, _popup, and main build() WebView.
    expect(mountArgs, hasLength(3));
    for (final callBody in mountArgs) {
      expect(
        callBody,
        contains('onRenderProcessGone:'),
        reason: 'Missing onRenderProcessGone on QuizPlayer InAppWebView mount site:\n$callBody',
      );
      expect(callBody, isNot(contains('onRenderProcessGone: null')));
    }
    expect(source, isNot(contains('identical(_controller, controller)')));
    expect(source, contains('_webViewGeneration'));

    // Distinct outer InAppWebViewController wrappers sharing one platform instance must match.
    final sharedPlatform = _FakeQuizPlatformWebViewController(11);
    final otherPlatform = _FakeQuizPlatformWebViewController(22);
    final createdWrapper =
        InAppWebViewController.fromPlatform(platform: sharedPlatform);
    final crashWrapper =
        InAppWebViewController.fromPlatform(platform: sharedPlatform);
    final otherWrapper =
        InAppWebViewController.fromPlatform(platform: otherPlatform);
    expect(identical(createdWrapper, crashWrapper), isFalse);
    expect(isSameQuizWebViewController(createdWrapper, crashWrapper), isTrue);
    expect(isSameQuizWebViewController(createdWrapper, otherWrapper), isFalse);
    expect(isSameQuizWebViewController(null, crashWrapper), isFalse);

    // Active main quiz WebView: stops load timeout, clears dead WebView state, emits
    // render_process_gone, and never auto-reloads a live quiz attempt.
    var stoppedTimers = 0;
    var clearedStates = 0;
    final emitted = <String>[];
    expect(
      handleQuizMainRenderProcessGone(
        disposed: false,
        mounted: true,
        callbackGeneration: 0,
        activeGeneration: 0,
        stopLoadTimeout: () => stoppedTimers++,
        clearDeadWebViewState: () => clearedStates++,
        emitError: emitted.add,
      ),
      isTrue,
    );
    expect(stoppedTimers, 1);
    expect(clearedStates, 1);
    expect(emitted, [quizRenderProcessGoneErrorCode]);
    expect(quizRenderProcessGoneErrorCode, 'render_process_gone');

    // Guarded when stale generation, unmounted, or disposed.
    expect(
      handleQuizMainRenderProcessGone(
        disposed: false,
        mounted: true,
        callbackGeneration: 0,
        activeGeneration: 1,
        stopLoadTimeout: () => stoppedTimers++,
        clearDeadWebViewState: () => clearedStates++,
        emitError: emitted.add,
      ),
      isFalse,
    );
    expect(
      handleQuizMainRenderProcessGone(
        disposed: false,
        mounted: false,
        callbackGeneration: 1,
        activeGeneration: 1,
        stopLoadTimeout: () => stoppedTimers++,
        clearDeadWebViewState: () => clearedStates++,
        emitError: emitted.add,
      ),
      isFalse,
    );
    expect(
      handleQuizMainRenderProcessGone(
        disposed: true,
        mounted: true,
        callbackGeneration: 1,
        activeGeneration: 1,
        stopLoadTimeout: () => stoppedTimers++,
        clearDeadWebViewState: () => clearedStates++,
        emitError: emitted.add,
      ),
      isFalse,
    );
    expect(stoppedTimers, 1);
    expect(clearedStates, 1);
    expect(emitted, hasLength(1));

    // Active quiz popup: closes dead popup and emits clean error only when mounted and alive.
    var closedPopups = 0;
    final popupErrors = <String>[];
    handleQuizPopupRenderProcessGone(
      disposed: false,
      mounted: true,
      closePopup: () => closedPopups++,
      emitError: popupErrors.add,
    );
    handleQuizPopupRenderProcessGone(
      disposed: false,
      mounted: false,
      closePopup: () => closedPopups++,
      emitError: popupErrors.add,
    );
    handleQuizPopupRenderProcessGone(
      disposed: true,
      mounted: true,
      closePopup: () => closedPopups++,
      emitError: popupErrors.add,
    );
    expect(closedPopups, 1);
    expect(popupErrors, [quizRenderProcessGoneErrorCode]);

    // Offstage blocked popup sink: swallows and removes sink safely without emitting error.
    var removedBlocked = 0;
    handleQuizBlockedPopupSinkRenderProcessGone(
      disposed: false,
      mounted: true,
      removeSink: () => removedBlocked++,
    );
    handleQuizBlockedPopupSinkRenderProcessGone(
      disposed: false,
      mounted: false,
      removeSink: () => removedBlocked++,
    );
    handleQuizBlockedPopupSinkRenderProcessGone(
      disposed: true,
      mounted: true,
      removeSink: () => removedBlocked++,
    );
    expect(removedBlocked, 1);
  });
}

class _FakeQuizPlatformWebViewController extends PlatformInAppWebViewController {
  _FakeQuizPlatformWebViewController(dynamic id)
      : super.implementation(
          PlatformInAppWebViewControllerCreationParams(id: id),
        );
}

List<String> _extractQuizInAppWebViewCalls(String source) {
  final results = <String>[];
  const marker = 'InAppWebView(';
  var searchFrom = 0;
  while (true) {
    final start = source.indexOf(marker, searchFrom);
    if (start < 0) break;
    var idx = start + marker.length;
    var depth = 1;
    while (idx < source.length && depth > 0) {
      final ch = source[idx];
      if (ch == '(') {
        depth++;
      } else if (ch == ')') {
        depth--;
      }
      idx++;
    }
    results.add(source.substring(start + marker.length, idx - 1));
    searchFrom = idx;
  }
  return results;
}
