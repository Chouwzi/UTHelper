import 'dart:async';

import 'package:flutter_inappwebview/flutter_inappwebview.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:flet_uth_quiz/src/app_webview_control.dart';
import 'package:flet_uth_quiz/src/app_webview_settings.dart';
import 'package:flet_uth_quiz/src/mobile_session_queue.dart';
import 'package:flet_uth_quiz/src/quiz_webview_settings.dart';

void main() {
  test('normalizeEmbeddedUserAgent strips Android wv markers while preserving Chrome and iOS Safari UAs', () {
    const androidWvUa =
        'Mozilla/5.0 (Linux; Android 14; SM-S918B Build/UP1A.231005.007; wv) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 '
        'Chrome/131.0.6778.135 Mobile Safari/537.36';
    expect(
      normalizeEmbeddedUserAgent(androidWvUa),
      'Mozilla/5.0 (Linux; Android 14; SM-S918B Build/UP1A.231005.007) '
      'AppleWebKit/537.36 (KHTML, like Gecko) '
      'Chrome/131.0.6778.135 Mobile Safari/537.36',
    );

    const iosUa =
        'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) '
        'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1';
    expect(normalizeEmbeddedUserAgent(iosUa), iosUa);
    expect(normalizeEmbeddedUserAgent(''), isNull);
    expect(normalizeEmbeddedUserAgent(null), isNull);
  });

  test('createAppWebViewSettings enables JS, DOM storage, 3P cookies, and hybrid composition in browser mode', () {
    final browser = createAppWebViewSettings(
      viewerLocked: false,
      userAgent: 'Mozilla/5.0 Chrome/131.0 Mobile Safari/537.36',
    );
    expect(browser.javaScriptEnabled, isTrue);
    expect(browser.domStorageEnabled, isTrue);
    expect(browser.databaseEnabled, isTrue);
    expect(browser.thirdPartyCookiesEnabled, isTrue);
    expect(browser.supportMultipleWindows, isTrue);
    expect(browser.javaScriptCanOpenWindowsAutomatically, isTrue);
    expect(browser.useHybridComposition, isTrue);
    expect(browser.hardwareAcceleration, isTrue);
    expect(browser.useShouldOverrideUrlLoading, isTrue);
    expect(browser.allowFileAccess, isFalse);
    expect(browser.allowContentAccess, isFalse);
    expect(browser.allowFileAccessFromFileURLs, isFalse);
    expect(browser.allowUniversalAccessFromFileURLs, isFalse);
    expect(browser.isInspectable, isFalse);
    expect(browser.mixedContentMode, MixedContentMode.MIXED_CONTENT_NEVER_ALLOW);
    expect(browser.regexToCancelSubFramesLoading, quizSubframeCancelRegex);
    expect(browser.userAgent, 'Mozilla/5.0 Chrome/131.0 Mobile Safari/537.36');

    final viewer = createAppWebViewSettings(
      viewerLocked: true,
      viewerPrefix: 'http://127.0.0.1:51234/pdf-viewer/tok456/',
    );
    expect(viewer.javaScriptEnabled, isTrue);
    expect(viewer.thirdPartyCookiesEnabled, isFalse);
    expect(viewer.supportMultipleWindows, isFalse);
    expect(viewer.javaScriptCanOpenWindowsAutomatically, isFalse);
    expect(viewer.cacheEnabled, isFalse);
    expect(viewer.cacheMode, CacheMode.LOAD_NO_CACHE);
    expect(viewer.regexToCancelSubFramesLoading, isNotNull);
    expect(
      viewer.regexToCancelSubFramesLoading!.startsWith('(?is)'),
      isTrue,
    );

    final sink = createAppWebViewSettings(
      viewerLocked: true,
      javaScriptEnabled: false,
    );
    expect(sink.javaScriptEnabled, isFalse);
    expect(sink.regexToCancelSubFramesLoading, isNotNull);
  });

  test('buildLockedViewerSubframeCancelRegex enforces locked viewer origin and about:blank only', () {
    final prefixRegexStr = buildLockedViewerSubframeCancelRegex(
      'http://127.0.0.1:51234/viewer/tok123/',
    );
    expect(prefixRegexStr.startsWith('(?is)'), isTrue);
    final prefixRegex = RegExp(
      prefixRegexStr.substring('(?is)'.length),
      caseSensitive: false,
      dotAll: true,
    );

    for (final allowed in [
      'about:blank',
      'http://127.0.0.1:51234/viewer/tok123/docx_host.html?src=x',
      'blob:http://127.0.0.1:51234/550e8400-e29b-41d4-a716-446655440000',
    ]) {
      expect(prefixRegex.hasMatch(allowed), isFalse, reason: allowed);
    }

    for (final cancelled in [
      'https://evil.example/exfil',
      'http://127.0.0.1:51234/other/path',
      'http://127.0.0.1:51234/viewer/tok123/../other',
      'http://127.0.0.1:51234/viewer/tok123/..%2Fother',
      'http://127.0.0.1:51234/viewer/tok123/%2e%2e/other',
      'http://127.0.0.1:9999/viewer/tok123/docx_host.html',
      'blob:http://127.0.0.1:9999/uuid',
      'blob:https://evil.example/uuid',
      'javascript:alert(1)',
      'data:text/html,<h1>x</h1>',
      'intent://evil/#Intent;end',
      'file:///etc/hosts',
      'http://127.0.0.1:51234/viewer/tok123/a\nhttp://evil',
    ]) {
      expect(prefixRegex.hasMatch(cancelled), isTrue, reason: cancelled);
    }

    // 16-hex exact-path item viewer (INT-02)
    final itemRegexStr = buildLockedViewerSubframeCancelRegex(
      'http://127.0.0.1:51234/1a25f20417cab3d2',
    );
    final itemRegex = RegExp(
      itemRegexStr.substring('(?is)'.length),
      caseSensitive: false,
      dotAll: true,
    );
    expect(itemRegex.hasMatch('about:blank'), isFalse);
    expect(
      itemRegex.hasMatch('http://127.0.0.1:51234/1a25f20417cab3d2?token=abc'),
      isFalse,
    );
    expect(
      itemRegex.hasMatch('http://127.0.0.1:51234/1a25f20417cab3d2/extra'),
      isTrue,
    );
    expect(
      itemRegex.hasMatch('http://127.0.0.1:51234/deadbeef00000000'),
      isTrue,
    );

    // Inline html= (viewerPrefix == null) allows about:blank only
    final inlineRegexStr = buildLockedViewerSubframeCancelRegex(null);
    final inlineRegex = RegExp(
      inlineRegexStr.substring('(?is)'.length),
      caseSensitive: false,
      dotAll: true,
    );
    expect(inlineRegex.hasMatch('about:blank'), isFalse);
    expect(inlineRegex.hasMatch('https://evil.example/'), isTrue);
    expect(inlineRegex.hasMatch('http://127.0.0.1:51234/'), isTrue);
  });

  test('extractViewerLockPrefix isolates local Office and PDF viewer origins', () {
    expect(
      extractViewerLockPrefix(
        'http://127.0.0.1:51234/viewer/tok123/docx_host.html?src=x',
      ),
      'http://127.0.0.1:51234/viewer/tok123/',
    );
    expect(
      extractViewerLockPrefix(
        'http://127.0.0.1:51234/pdf-viewer/tok456/pdf_host.html?src=y',
      ),
      'http://127.0.0.1:51234/pdf-viewer/tok456/',
    );
    expect(
      extractViewerLockPrefix(
        'http://127.0.0.1:51234/1a25f20417cab3d2?token=secret',
      ),
      'http://127.0.0.1:51234/1a25f20417cab3d2',
    );
    expect(
      extractViewerLockPrefix('https://courses.ut.edu.vn/course/view.php?id=1'),
      isNull,
    );
  });

  test('resolveAndroidIntentFallbackUrl extracts only safe HTTPS fallback URLs', () {
    const sharePointIntent =
        'intent://uteduvn-my.sharepoint.com/:x:/g/personal/doc#Intent;'
        'scheme=ms-excel;package=com.microsoft.office.excel;'
        'S.browser_fallback_url=https%3A%2F%2Futeduvn-my.sharepoint.com%2F%3Ax%3A%2Fg%2Fpersonal%2Fdoc%3Fe%3Drs8b4a;end';
    expect(
      resolveAndroidIntentFallbackUrl(sharePointIntent),
      'https://uteduvn-my.sharepoint.com/:x:/g/personal/doc?e=rs8b4a',
    );

    const unsafeIntent =
        'intent://evil#Intent;S.browser_fallback_url=javascript%3Aalert(1);end';
    expect(resolveAndroidIntentFallbackUrl(unsafeIntent), isNull);

    const httpIntent =
        'intent://evil#Intent;S.browser_fallback_url=http%3A%2F%2Fevil.example%2F;end';
    expect(resolveAndroidIntentFallbackUrl(httpIntent), isNull);

    const ssrfIntent =
        'intent://evil#Intent;S.browser_fallback_url=https%3A%2F%2F169.254.169.254%2Flatest;end';
    expect(resolveAndroidIntentFallbackUrl(ssrfIntent), isNull);
  });

  test('decideAppWebViewNavigation enforces viewer lock and browser security rules', () {
    const prefix = 'http://127.0.0.1:51234/pdf-viewer/tok/';
    expect(
      decideAppWebViewNavigation(
        'http://127.0.0.1:51234/pdf-viewer/tok/pdf_host.html',
        isForMainFrame: true,
        viewerLocked: true,
        viewerPrefix: prefix,
      ).kind,
      AppNavigationKind.allow,
    );
    expect(
      decideAppWebViewNavigation(
        'blob:http://127.0.0.1:51234/abc-123',
        isForMainFrame: false,
        viewerLocked: true,
        viewerPrefix: prefix,
      ).kind,
      AppNavigationKind.allow,
    );
    expect(
      decideAppWebViewNavigation(
        'https://uteduvn-my.sharepoint.com/',
        isForMainFrame: true,
        viewerLocked: true,
        viewerPrefix: prefix,
      ).kind,
      AppNavigationKind.cancel,
    );

    // Browser mode allows SharePoint, Microsoft login, Moodle, and allowed loopback
    for (final safeUrl in [
      'https://courses.ut.edu.vn/mod/url/view.php?id=12',
      'https://uteduvn-my.sharepoint.com/:x:/g/personal/abc?e=rs8b4a',
      'https://login.microsoftonline.com/common/oauth2/v2.0/authorize',
      'http://127.0.0.1:59001/page?token=abc',
    ]) {
      expect(
        decideAppWebViewNavigation(
          safeUrl,
          isForMainFrame: true,
          viewerLocked: false,
          allowedLoopbackPorts: const {59001},
        ).kind,
        AppNavigationKind.allow,
        reason: safeUrl,
      );
    }

    // Redirects Android intent:// with HTTPS fallback in-place on main frame
    final intentDecision = decideAppWebViewNavigation(
      'intent://zalo.me/g/abc#Intent;scheme=zalo;S.browser_fallback_url=https%3A%2F%2Fzalo.me%2Fg%2Fabc;end',
      isForMainFrame: true,
      viewerLocked: false,
    );
    expect(intentDecision.kind, AppNavigationKind.redirectInPlace);
    expect(intentDecision.targetUrl, 'https://zalo.me/g/abc');

    // Blocks unsafe schemes, private/loopback HTTPS SSRF, and unapproved HTTP/loopback ports
    for (final blocked in [
      'javascript:alert(1)',
      'file:///etc/hosts',
      'data:text/html,<h1>x</h1>',
      'http://evil.example/phish',
      'http://127.0.0.1:44444/other',
      'https://user:pass@courses.ut.edu.vn/',
      'https://127.0.0.1/admin',
      'https://localhost:8443/debug',
      'https://foo.localhost/debug',
      'https://printer.local/',
      'https://10.0.0.1/internal',
      'https://172.16.0.1/internal',
      'https://192.168.1.1/router',
      'https://169.254.169.254/latest/meta-data/',
      'https://100.64.0.1/cgnat',
      'https://[::1]/admin',
      'https://[::ffff:127.0.0.1]/admin',
      'https://[fe80::1]/',
      'https://[fc00::1]/',
      'https://2130706433/',
      'https://0177.0.0.1/',
      'https://0x7f000001/',
      'https://127.1/',
      'blob:https://127.0.0.1/uuid',
    ]) {
      expect(
        decideAppWebViewNavigation(
          blocked,
          isForMainFrame: true,
          viewerLocked: false,
          allowedLoopbackPorts: const {59001},
        ).kind,
        AppNavigationKind.cancel,
        reason: blocked,
      );
    }

    // Viewer-locked blocks percent-encoded traversal, empty-userinfo @, and sibling 16-hex paths
    for (final lockedBlocked in [
      'http://127.0.0.1:51234/pdf-viewer/tok/..%2F..%2Fsecret',
      'http://127.0.0.1:51234/pdf-viewer/tok/%2e%2e/secret',
      'http://127.0.0.1:51234/pdf-viewer/tok/a%5cb',
      'http://@127.0.0.1:51234/pdf-viewer/tok/pdf_host.html',
    ]) {
      expect(
        decideAppWebViewNavigation(
          lockedBlocked,
          isForMainFrame: true,
          viewerLocked: true,
          viewerPrefix: prefix,
        ).kind,
        AppNavigationKind.cancel,
        reason: lockedBlocked,
      );
    }

    const itemPrefix = 'http://127.0.0.1:51234/1a25f20417cab3d2';
    expect(
      decideAppWebViewNavigation(
        'http://127.0.0.1:51234/1a25f20417cab3d2?token=abc',
        isForMainFrame: true,
        viewerLocked: true,
        viewerPrefix: itemPrefix,
      ).kind,
      AppNavigationKind.allow,
    );
    expect(
      decideAppWebViewNavigation(
        'http://127.0.0.1:51234/1a25f20417cab3d2/extra',
        isForMainFrame: true,
        viewerLocked: true,
        viewerPrefix: itemPrefix,
      ).kind,
      AppNavigationKind.cancel,
    );
    expect(
      decideAppWebViewNavigation(
        'https://courses.ut.edu.vn/',
        isForMainFrame: true,
        viewerLocked: true,
        viewerPrefix: itemPrefix,
      ).kind,
      AppNavigationKind.cancel,
    );
  });

  test('validateAppWebViewCookie enforces exact-host cookies and rejects attribute/parent-domain injection', () {
    final target = Uri.parse('https://courses.ut.edu.vn/mod/url/view.php?id=9');
    final hostOnly = validateAppWebViewCookie(
      {
        'name': 'MoodleSession',
        'value': 'sess_abc123',
        'domain': 'courses.ut.edu.vn.local',
        'path': '/',
        'secure': false,
        'httpOnly': true,
        'expires': null,
      },
      target,
    );
    expect(hostOnly, isNotNull);
    expect(hostOnly!.name, 'MoodleSession');
    expect(hostOnly.value, 'sess_abc123');
    expect(hostOnly.domain, isNull);
    expect(hostOnly.isSecure, isTrue);
    expect(hostOnly.isHttpOnly, isTrue);

    final dottedExactHost = validateAppWebViewCookie(
      {
        'name': 'MoodleID1_',
        'value': 'val1',
        'domain': '.courses.ut.edu.vn',
        'path': '/',
        'secure': true,
        'http_only': false,
      },
      target,
    );
    expect(dottedExactHost, isNotNull);
    expect(dottedExactHost!.domain, isNull);

    for (final badCookie in [
      // Parent domains / public suffixes must be rejected (WV-02)
      {
        'name': 'MoodleSession',
        'value': 'tok',
        'domain': '.ut.edu.vn',
        'path': '/',
        'secure': true,
      },
      {
        'name': 'MoodleSession',
        'value': 'tok',
        'domain': '.edu.vn',
        'path': '/',
        'secure': true,
      },
      {
        'name': 'MoodleSession',
        'value': 'steal',
        'domain': 'evil.example',
        'path': '/',
        'secure': true,
      },
      // Set-Cookie attribute injection in path or unsafe chars in value (WV-02)
      {
        'name': 'MoodleSession',
        'value': 'tok',
        'domain': 'courses.ut.edu.vn',
        'path': '/; Domain=ut.edu.vn; SameSite=None',
        'secure': true,
      },
      {
        'name': 'MoodleSession',
        'value': 'tok',
        'domain': 'courses.ut.edu.vn',
        'path': '/bad path',
        'secure': true,
      },
      {
        'name': 'MoodleSession',
        'value': 'tok with space',
        'domain': 'courses.ut.edu.vn',
        'path': '/',
        'secure': true,
      },
      {
        'name': 'MoodleSession',
        'value': 'a,b',
        'domain': 'courses.ut.edu.vn',
        'path': '/',
        'secure': true,
      },
    ]) {
      expect(
        validateAppWebViewCookie(badCookie, target),
        isNull,
        reason: '$badCookie',
      );
    }
  });

  test('resolveCreateWindowInPlaceTarget skips about:blank and blob: targets (INT-01)', () {
    expect(
      resolveCreateWindowInPlaceTarget('about:blank', viewerLocked: false),
      isNull,
    );
    expect(
      resolveCreateWindowInPlaceTarget('ABOUT:BLANK', viewerLocked: false),
      isNull,
    );
    expect(
      resolveCreateWindowInPlaceTarget(
        'blob:https://courses.ut.edu.vn/uuid',
        viewerLocked: false,
      ),
      isNull,
    );
    expect(
      resolveCreateWindowInPlaceTarget(
        'https://uteduvn-my.sharepoint.com/doc',
        viewerLocked: true,
      ),
      isNull,
    );
    expect(
      resolveCreateWindowInPlaceTarget(
        'https://uteduvn-my.sharepoint.com/doc',
        viewerLocked: false,
      ),
      'https://uteduvn-my.sharepoint.com/doc',
    );
    expect(
      resolveCreateWindowInPlaceTarget(
        'intent://zalo.me/g/abc#Intent;scheme=zalo;S.browser_fallback_url=https%3A%2F%2Fzalo.me%2Fg%2Fabc;end',
        viewerLocked: false,
      ),
      'https://zalo.me/g/abc',
    );
  });

  test('shouldForwardAppConsoleMessage requires viewerLocked and bounds length to 4096 (WV-05)', () {
    expect(
      shouldForwardAppConsoleMessage(
        '__UTH_MSG__:{"type":"ready"}',
        viewerLocked: true,
      ),
      isTrue,
    );
    expect(
      shouldForwardAppConsoleMessage(
        '__UTH_MSG__:{"type":"ready"}',
        viewerLocked: false,
      ),
      isFalse,
    );
    expect(
      shouldForwardAppConsoleMessage(
        '__UTH_MSG__:${'a' * 4096}',
        viewerLocked: true,
      ),
      isFalse,
    );
    expect(
      shouldForwardAppConsoleMessage('regular log', viewerLocked: true),
      isFalse,
    );
  });

  test('installAppWebViewCookies serializes via MobileSessionQueue, pins initial host, and aborts on dispose (WV-02)', () async {
    final queue = MobileSessionQueue();
    final blocker = await queue.acquire(cancelled: Completer<void>().future);
    expect(blocker, isNotNull);

    final installed = <String>[];
    final initial = Uri.parse('https://courses.ut.edu.vn/mod/url/view.php?id=1');
    final cookies = [
      {
        'name': 'MoodleSession',
        'value': 'sess1',
        'domain': 'courses.ut.edu.vn',
        'path': '/',
        'secure': true,
        'httpOnly': true,
      },
    ];

    final installFuture = installAppWebViewCookies(
      rawCookies: cookies,
      targetUri: initial,
      initialUri: initial,
      queue: queue,
      isDisposed: () => false,
      disposeSignal: Completer<void>().future,
      setCookie: (url, cookie) async {
        installed.add('${url.toString()}:${cookie.name}');
      },
    );

    // Must wait while cleaner/quiz holds MobileSessionQueue lease
    await Future<void>.delayed(const Duration(milliseconds: 20));
    expect(installed, isEmpty);

    blocker!.release();
    final count = await installFuture.timeout(const Duration(seconds: 2));
    expect(count, 1);
    expect(installed, ['https://courses.ut.edu.vn/:MoodleSession']);

    // Cross-host targetUri must install zero cookies
    installed.clear();
    final crossHostCount = await installAppWebViewCookies(
      rawCookies: cookies,
      targetUri: Uri.parse('https://evil.example/'),
      initialUri: initial,
      queue: queue,
      isDisposed: () => false,
      disposeSignal: Completer<void>().future,
      setCookie: (url, cookie) async {
        installed.add(cookie.name);
      },
    );
    expect(crossHostCount, 0);
    expect(installed, isEmpty);

    // Disposed while waiting on queue writes zero cookies
    final blocker2 = await queue.acquire(cancelled: Completer<void>().future);
    final disposeCompleter = Completer<void>();
    var disposed = false;
    final cancelledFuture = installAppWebViewCookies(
      rawCookies: cookies,
      targetUri: initial,
      initialUri: initial,
      queue: queue,
      isDisposed: () => disposed,
      disposeSignal: disposeCompleter.future,
      setCookie: (url, cookie) async {
        installed.add(cookie.name);
      },
    );
    await Future<void>.delayed(const Duration(milliseconds: 20));
    disposed = true;
    disposeCompleter.complete();
    final cancelledCount = await cancelledFuture;
    blocker2!.release();
    expect(cancelledCount, 0);
    expect(installed, isEmpty);
  });

  test('appWebViewCookieWaitTimeout is 10s and late-cookie reload fires once after initial nav (WV-06)', () {
    expect(appWebViewCookieWaitTimeout, const Duration(seconds: 10));
    expect(
      shouldReloadAfterLateCookies(
        appliedCount: 1,
        initialNavDispatched: true,
        alreadyReloaded: false,
        disposed: false,
        hasController: true,
        initialUrl: 'https://courses.ut.edu.vn/course/view.php?id=9',
      ),
      isTrue,
    );
    expect(
      shouldReloadAfterLateCookies(
        appliedCount: 0,
        initialNavDispatched: true,
        alreadyReloaded: false,
        disposed: false,
        hasController: true,
        initialUrl: 'https://courses.ut.edu.vn/course/view.php?id=9',
      ),
      isFalse,
    );
    expect(
      shouldReloadAfterLateCookies(
        appliedCount: 1,
        initialNavDispatched: true,
        alreadyReloaded: true,
        disposed: false,
        hasController: true,
        initialUrl: 'https://courses.ut.edu.vn/course/view.php?id=9',
      ),
      isFalse,
    );
  });

  test('trimPopupSinksToCap enforces maxAppPopupSinks=4 and cancels evicted timers (WV-05)', () {
    expect(maxAppPopupSinks, 4);
    final keys = <int>[1, 2, 3, 4];
    final cancelled = <int>[];
    trimPopupSinksToCap(
      keys,
      maxCount: maxAppPopupSinks,
      onEvict: cancelled.add,
    );
    expect(keys, [2, 3, 4]);
    expect(cancelled, [1]);
  });

  test('shouldReportMainFrameWebError ignores subframe errors and range stream aborts', () {
    expect(
      shouldReportMainFrameWebError(
        isForMainFrame: false,
        errorType: WebResourceErrorType.HOST_LOOKUP,
        description: 'net::ERR_NAME_NOT_RESOLVED',
      ),
      isFalse,
    );
    expect(
      shouldReportMainFrameWebError(
        isForMainFrame: true,
        errorType: WebResourceErrorType.CANCELLED,
        description: 'net::ERR_ABORTED',
      ),
      isFalse,
    );
    expect(
      shouldReportMainFrameWebError(
        isForMainFrame: true,
        errorType: WebResourceErrorType.HOST_LOOKUP,
        description: 'net::ERR_NAME_NOT_RESOLVED',
      ),
      isTrue,
    );
  });
}
