import 'dart:async';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter_inappwebview/flutter_inappwebview.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:flet_uth_quiz/src/app_webview_control.dart';
import 'package:flet_uth_quiz/src/app_webview_settings.dart';
import 'package:flet_uth_quiz/src/mobile_session_queue.dart';
import 'package:flet_uth_quiz/src/quiz_session_cleaner.dart';

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
    expect(browser.useOnDownloadStart, isTrue);
    expect(browser.allowFileAccess, isFalse);
    expect(browser.allowContentAccess, isFalse);
    expect(browser.allowFileAccessFromFileURLs, isFalse);
    expect(browser.allowUniversalAccessFromFileURLs, isFalse);
    expect(browser.isInspectable, isFalse);
    expect(browser.mixedContentMode, MixedContentMode.MIXED_CONTENT_NEVER_ALLOW);
    expect(browser.regexToCancelSubFramesLoading, appBrowserSubframeCancelRegex);
    expect(browser.userAgent, 'Mozilla/5.0 Chrome/131.0 Mobile Safari/537.36');

    final viewer = createAppWebViewSettings(
      viewerLocked: true,
      viewerPrefix: 'http://127.0.0.1:51234/pdf-viewer/tok456/',
    );
    expect(viewer.javaScriptEnabled, isTrue);
    expect(viewer.thirdPartyCookiesEnabled, isFalse);
    expect(viewer.supportMultipleWindows, isFalse);
    expect(viewer.javaScriptCanOpenWindowsAutomatically, isFalse);
    expect(viewer.useOnDownloadStart, isFalse);
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

    // Browser mode allows SharePoint, Microsoft login, Moodle, Google Sheets #gid fragments, and allowed loopback
    for (final safeUrl in [
      'https://courses.ut.edu.vn/mod/url/view.php?id=12',
      'https://courses.ut.edu.vn/course/view.php?id=12#section-3',
      'https://docs.google.com/spreadsheets/d/1y5syAENL/edit?gid=1012692952#gid=1012692952',
      'https://docs.google.com/spreadsheets/d/1y5syAENL/htmlview#gid=1012692952',
      'https://uteduvn-my.sharepoint.com/:x:/g/personal/abc?e=rs8b4a',
      'https://login.microsoftonline.com/common/oauth2/v2.0/authorize#state=1',
      'http://127.0.0.1:59001/page?token=abc#anchor',
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

  test('appWebViewCookieWaitTimeout is bounded to 4s and late-cookie reload skips cross-host redirects (WV-06)', () {
    expect(appWebViewCookieWaitTimeout, const Duration(seconds: 4));
    expect(
      shouldReloadAfterLateCookies(
        appliedCount: 1,
        initialNavDispatched: true,
        alreadyReloaded: false,
        disposed: false,
        hasController: true,
        initialUrl: 'https://courses.ut.edu.vn/course/view.php?id=9',
        currentLoadedUrl: 'https://courses.ut.edu.vn/login/index.php',
      ),
      isTrue,
    );
    // Must NOT yank user back to Moodle if 302 redirect already landed on SharePoint/Microsoft SSO!
    expect(
      shouldReloadAfterLateCookies(
        appliedCount: 1,
        initialNavDispatched: true,
        alreadyReloaded: false,
        disposed: false,
        hasController: true,
        initialUrl: 'https://courses.ut.edu.vn/mod/url/view.php?id=99',
        currentLoadedUrl: 'https://uteduvn-my.sharepoint.com/:x:/g/personal/doc',
      ),
      isFalse,
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

  test('decideAppWebViewNavigation preserves case-sensitive token_urlsafe viewer prefixes while lowercasing scheme and host', () {
    const mixedTokenUrl =
        'http://127.0.0.1:51234/pdf-viewer/AbCdEfGh_IjKlMn-OpQrStUvWxYz0123/pdf_host.html?src=%2F1a25f20417cab3d2%3Ftoken%3DxYz_987';
    final mixedPrefix = extractViewerLockPrefix(mixedTokenUrl);
    expect(
      mixedPrefix,
      'http://127.0.0.1:51234/pdf-viewer/AbCdEfGh_IjKlMn-OpQrStUvWxYz0123/',
    );
    expect(
      decideAppWebViewNavigation(
        mixedTokenUrl,
        isForMainFrame: true,
        viewerLocked: true,
        viewerPrefix: mixedPrefix,
      ).kind,
      AppNavigationKind.allow,
    );
    expect(
      decideAppWebViewNavigation(
        'HTTP://127.0.0.1:51234/pdf-viewer/AbCdEfGh_IjKlMn-OpQrStUvWxYz0123/pdf.worker.min.mjs',
        isForMainFrame: false,
        viewerLocked: true,
        viewerPrefix: mixedPrefix,
      ).kind,
      AppNavigationKind.allow,
    );

    const docxUrl =
        'http://LOCALHOST:51234/viewer/ZxYwVuTsRqPoNmLkJiHgFeDcBa987654/docx_host.html?src=%2Fdeadbeefcafebabe%3Ftoken%3DT0k';
    final docxPrefix = extractViewerLockPrefix(docxUrl);
    expect(
      docxPrefix,
      'http://localhost:51234/viewer/ZxYwVuTsRqPoNmLkJiHgFeDcBa987654/',
    );
    expect(
      decideAppWebViewNavigation(
        docxUrl,
        isForMainFrame: true,
        viewerLocked: true,
        viewerPrefix: docxPrefix,
      ).kind,
      AppNavigationKind.allow,
    );

    // Wrong-case token must still be rejected
    expect(
      decideAppWebViewNavigation(
        'http://127.0.0.1:51234/pdf-viewer/abcdefgh_ijklmn-opqrstuvwxyz0123/pdf_host.html',
        isForMainFrame: true,
        viewerLocked: true,
        viewerPrefix: mixedPrefix,
      ).kind,
      AppNavigationKind.cancel,
    );
  });

  test('appBrowserSubframeCancelRegex allows SharePoint/Office Online about:srcdoc and blob:https subframes while cancelling unsafe schemes', () {
    expect(appBrowserSubframeCancelRegex.startsWith('(?is)'), isTrue);
    final regex = RegExp(
      appBrowserSubframeCancelRegex.substring('(?is)'.length),
      caseSensitive: false,
      dotAll: true,
    );

    for (final allowed in [
      'https://excel.officeapps.live.com/x/_layouts/xlviewerinternal.aspx?ui=vi-VN',
      'https://login.microsoftonline.com/common/oauth2/v2.0/authorize',
      'about:blank',
      'about:blank#wac',
      'about:srcdoc',
      'about:srcdoc#frame1',
      'blob:https://excel.officeapps.live.com/550e8400-e29b-41d4-a716-446655440000',
      'blob:https://docs.google.com/12345678-90ab-cdef',
    ]) {
      expect(regex.hasMatch(allowed), isFalse, reason: allowed);
    }

    for (final cancelled in [
      'javascript:alert(1)',
      'data:text/html,<script>alert(1)</script>',
      'file:///etc/hosts',
      'intent://uteduvn-my.sharepoint.com/#Intent;scheme=https;end',
      'http://evil.example/frame',
      'http://127.0.0.1:51234/pdf-viewer/tok/pdf_host.html',
      'blob:http://127.0.0.1:51234/550e8400-e29b',
      'blob:http://evil.example/550e8400-e29b',
      'https://user:pass@courses.ut.edu.vn/',
      'https://127.0.0.1/admin',
      'https://localhost:8443/',
      'https://10.0.0.1/internal',
      'https://192.168.1.1/',
      'https://169.254.169.254/latest',
      'https://[::1]/',
      'blob:https://127.0.0.1/uuid',
      'blob:https://localhost/uuid',
    ]) {
      expect(regex.hasMatch(cancelled), isTrue, reason: cancelled);
    }

    expect(
      decideAppWebViewNavigation(
        'about:srcdoc',
        isForMainFrame: false,
        viewerLocked: false,
      ).kind,
      AppNavigationKind.allow,
    );
    expect(
      resolveCreateWindowInPlaceTarget('about:srcdoc', viewerLocked: false),
      isNull,
    );
  });

  test('resolveAndroidIntentFallbackUrl synthesizes HTTPS URL when scheme=https and S.browser_fallback_url is absent', () {
    const driveIntent =
        'intent://docs.google.com/spreadsheets/d/1AbC_dEf/edit?usp=sharing#Intent;'
        'scheme=https;package=com.google.android.apps.docs.editors.sheets;end';
    expect(
      resolveAndroidIntentFallbackUrl(driveIntent),
      'https://docs.google.com/spreadsheets/d/1AbC_dEf/edit?usp=sharing',
    );

    const sharePointHttpsIntent =
        'intent://uteduvn-my.sharepoint.com/:x:/g/personal/user_ut_edu_vn/Doc1?e=rs8b4a#Intent;'
        'package=com.microsoft.office.excel;scheme=HTTPS;end';
    expect(
      resolveAndroidIntentFallbackUrl(sharePointHttpsIntent),
      'https://uteduvn-my.sharepoint.com/:x:/g/personal/user_ut_edu_vn/Doc1?e=rs8b4a',
    );

    // Custom non-https scheme without S.browser_fallback_url must return null
    const customSchemeOnly =
        'intent://uteduvn-my.sharepoint.com/doc#Intent;scheme=ms-excel;package=com.microsoft.office.excel;end';
    expect(resolveAndroidIntentFallbackUrl(customSchemeOnly), isNull);

    // Explicit unsafe S.browser_fallback_url must NOT fall back to scheme=https
    const explicitUnsafeFallback =
        'intent://docs.google.com/doc#Intent;scheme=https;S.browser_fallback_url=javascript%3Aalert(1);end';
    expect(resolveAndroidIntentFallbackUrl(explicitUnsafeFallback), isNull);

    // SSRF / userinfo targets in scheme=https intent must be rejected
    expect(
      resolveAndroidIntentFallbackUrl(
        'intent://127.0.0.1/admin#Intent;scheme=https;end',
      ),
      isNull,
    );
    expect(
      resolveAndroidIntentFallbackUrl(
        'intent://user:pass@docs.google.com/doc#Intent;scheme=https;end',
      ),
      isNull,
    );
  });

  test('resolveDownloadExternalUrl allows only public HTTPS downloads in browser mode', () {
    expect(
      resolveDownloadExternalUrl(
        'https://uteduvn-my.sharepoint.com/:x:/g/personal/u/file.xlsx?download=1',
        viewerLocked: false,
      ),
      'https://uteduvn-my.sharepoint.com/:x:/g/personal/u/file.xlsx?download=1',
    );
    expect(
      resolveDownloadExternalUrl(
        'https://uteduvn-my.sharepoint.com/:x:/g/personal/u/file.xlsx?download=1',
        viewerLocked: true,
      ),
      isNull,
    );
    for (final blocked in [
      null,
      '',
      'blob:https://excel.officeapps.live.com/uuid',
      'data:application/pdf;base64,JVBERi0=',
      'http://127.0.0.1:51234/1a25f20417cab3d2?token=abc',
      'https://127.0.0.1/file.pdf',
      'https://192.168.1.1/file.pdf',
    ]) {
      expect(
        resolveDownloadExternalUrl(blocked, viewerLocked: false),
        isNull,
        reason: '$blocked',
      );
    }
  });

  test('shouldNavigateOnWidgetUrlSync avoids echo reloads while navigating on genuine URL changes', () {
    // Echo of URL already loaded in WebView must NOT trigger reload
    expect(
      shouldNavigateOnWidgetUrlSync(
        nextUrl: 'https://uteduvn-my.sharepoint.com/:x:/g/personal/doc',
        previousInitialUrl: 'https://courses.ut.edu.vn/mod/url/view.php?id=99',
        currentLoadedUrl: 'https://uteduvn-my.sharepoint.com/:x:/g/personal/doc',
      ),
      isFalse,
    );
    // Genuine new URL must navigate
    expect(
      shouldNavigateOnWidgetUrlSync(
        nextUrl: 'https://docs.google.com/document/d/2',
        previousInitialUrl: 'https://courses.ut.edu.vn/mod/url/view.php?id=99',
        currentLoadedUrl: 'https://uteduvn-my.sharepoint.com/:x:/g/personal/doc',
      ),
      isTrue,
    );
    expect(
      shouldNavigateOnWidgetUrlSync(
        nextUrl: '',
        previousInitialUrl: 'https://courses.ut.edu.vn/',
        currentLoadedUrl: 'https://courses.ut.edu.vn/',
      ),
      isFalse,
    );
  });

  test('shouldAdoptAppPopupWindowSink adopts Android, iOS, and Windows popup windowIds so JS window.open resolves cleanly', () {
    expect(shouldAdoptAppPopupWindowSink(TargetPlatform.android), isTrue);
    expect(shouldAdoptAppPopupWindowSink(TargetPlatform.iOS), isTrue);
    expect(shouldAdoptAppPopupWindowSink(TargetPlatform.windows), isTrue);
    expect(shouldAdoptAppPopupWindowSink(TargetPlatform.linux), isFalse);
  });

  test('shouldUseCleanAndroidMainFrameLoad strips stale redirect headers ONLY on cross-origin Android GET HTTPS navigations and never on initial or same-origin redirects', () {
    // Cross-origin Android main-frame GET HTTPS in browser mode must reload cleanly without
    // forwarding InAppWebViewClientCompat's stale cross-origin request headers.
    expect(
      shouldUseCleanAndroidMainFrameLoad(
        platform: TargetPlatform.android,
        viewerLocked: false,
        isForMainFrame: true,
        decisionKind: AppNavigationKind.allow,
        rawUrl: 'https://www.office.com/login',
        method: 'GET',
        currentUrl: 'https://login.microsoftonline.com/common/oauth2/v2.0/authorize',
      ),
      isTrue,
    );
    expect(
      shouldUseCleanAndroidMainFrameLoad(
        platform: TargetPlatform.android,
        viewerLocked: false,
        isForMainFrame: true,
        decisionKind: AppNavigationKind.allow,
        rawUrl: 'https://uteduvn-my.sharepoint.com/:x:/g/personal/doc',
        method: null,
        currentUrl: 'https://courses.ut.edu.vn/mod/url/view.php?id=99',
      ),
      isTrue,
    );
    expect(
      shouldUseCleanAndroidMainFrameLoad(
        platform: TargetPlatform.android,
        viewerLocked: false,
        isForMainFrame: true,
        decisionKind: AppNavigationKind.allow,
        rawUrl: 'https://login.microsoftonline.com/common/oauth2/v2.0/authorize',
        method: 'GET',
        currentUrl: 'https://uteduvn-my.sharepoint.com/:x:/g/personal/doc',
      ),
      isTrue,
    );
    // Initial load (empty/null currentUrl) and same-origin redirects (e.g. Google Docs
    // /edit?gid=...#gid=... -> 302 -> /htmlview#gid=...) MUST NOT cancel and re-issue loadUrl!
    expect(
      shouldUseCleanAndroidMainFrameLoad(
        platform: TargetPlatform.android,
        viewerLocked: false,
        isForMainFrame: true,
        decisionKind: AppNavigationKind.allow,
        rawUrl: 'https://docs.google.com/spreadsheets/d/1y5syAENL/edit?gid=1012692952#gid=1012692952',
        method: 'GET',
      ),
      isFalse,
    );
    expect(
      shouldUseCleanAndroidMainFrameLoad(
        platform: TargetPlatform.android,
        viewerLocked: false,
        isForMainFrame: true,
        decisionKind: AppNavigationKind.allow,
        rawUrl: 'https://docs.google.com/spreadsheets/d/1y5syAENL/htmlview#gid=1012692952',
        method: 'GET',
        currentUrl: 'https://docs.google.com/spreadsheets/d/1y5syAENL/edit?gid=1012692952#gid=1012692952',
      ),
      isFalse,
    );
    expect(
      shouldUseCleanAndroidMainFrameLoad(
        platform: TargetPlatform.android,
        viewerLocked: false,
        isForMainFrame: true,
        decisionKind: AppNavigationKind.allow,
        rawUrl: 'https://courses.ut.edu.vn/course/view.php?id=12',
        method: 'GET',
        currentUrl: 'https://courses.ut.edu.vn/my/',
      ),
      isFalse,
    );
    // POST, subframes, locked viewers, non-HTTPS (about:/blob:), and non-Android
    // platforms must NOT intercept via clean loadUrl.
    expect(
      shouldUseCleanAndroidMainFrameLoad(
        platform: TargetPlatform.android,
        viewerLocked: false,
        isForMainFrame: true,
        decisionKind: AppNavigationKind.allow,
        rawUrl: 'https://uteduvn-my.sharepoint.com/_forms/default.aspx',
        method: 'POST',
        currentUrl: 'https://login.microsoftonline.com/common/login',
      ),
      isFalse,
    );
    expect(
      shouldUseCleanAndroidMainFrameLoad(
        platform: TargetPlatform.android,
        viewerLocked: false,
        isForMainFrame: false,
        decisionKind: AppNavigationKind.allow,
        rawUrl: 'https://ffc-excel.officeapps.live.com/wv/wordviewerframe.aspx',
        method: 'GET',
        currentUrl: 'https://uteduvn-my.sharepoint.com/:x:/g/personal/doc',
      ),
      isFalse,
    );
    expect(
      shouldUseCleanAndroidMainFrameLoad(
        platform: TargetPlatform.android,
        viewerLocked: true,
        isForMainFrame: true,
        decisionKind: AppNavigationKind.allow,
        rawUrl: 'http://127.0.0.1:43125/viewer/Token/pdf_host.html',
        method: 'GET',
        currentUrl: 'http://127.0.0.1:43125/other',
      ),
      isFalse,
    );
    expect(
      shouldUseCleanAndroidMainFrameLoad(
        platform: TargetPlatform.android,
        viewerLocked: false,
        isForMainFrame: true,
        decisionKind: AppNavigationKind.allow,
        rawUrl: 'about:blank',
        method: 'GET',
        currentUrl: 'https://docs.google.com/spreadsheets/d/1/edit',
      ),
      isFalse,
    );
    expect(
      shouldUseCleanAndroidMainFrameLoad(
        platform: TargetPlatform.iOS,
        viewerLocked: false,
        isForMainFrame: true,
        decisionKind: AppNavigationKind.allow,
        rawUrl: 'https://www.office.com/login',
        method: 'GET',
        currentUrl: 'https://login.microsoftonline.com/common/oauth2/v2.0/authorize',
      ),
      isFalse,
    );
  });

  test('normalizeEmbeddedUserAgent omits Mobile token on Android tablets while keeping Mobile on phones', () {
    const tabletWvUa =
        'Mozilla/5.0 (Linux; Android 15; SM-X826B Build/AP3A.240905.015.A2; wv) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 '
        'Chrome/157.0.8103.50 Mobile Safari/537.36';
    expect(
      normalizeEmbeddedUserAgent(tabletWvUa, isTablet: true),
      'Mozilla/5.0 (Linux; Android 15; SM-X826B Build/AP3A.240905.015.A2) '
      'AppleWebKit/537.36 (KHTML, like Gecko) '
      'Chrome/157.0.8103.50 Safari/537.36',
    );
    expect(
      normalizeEmbeddedUserAgent(tabletWvUa, isTablet: false),
      'Mozilla/5.0 (Linux; Android 15; SM-X826B Build/AP3A.240905.015.A2) '
      'AppleWebKit/537.36 (KHTML, like Gecko) '
      'Chrome/157.0.8103.50 Mobile Safari/537.36',
    );
  });

  test('resolveAndroidIntentFallbackUrl handles market:// fallback with scheme=https and prevents self-redirect loops', () {
    // Google Docs /htmlview "Sử dụng ứng dụng" banner includes S.browser_fallback_url=market://... AND scheme=https
    const docsBannerIntent =
        'intent://docs.google.com/spreadsheets/d/1y5syAENL/edit?gid=10#Intent;'
        'S.browser_fallback_url=market%3A%2F%2Fdetails%3Fid%3Dcom.google.android.apps.docs.editors.sheets;'
        'package=com.google.android.apps.docs.editors.sheets;scheme=https;end';
    expect(
      resolveAndroidIntentFallbackUrl(
        docsBannerIntent,
        currentUrl: 'https://docs.google.com/spreadsheets/d/1y5syAENL/htmlview',
      ),
      'https://docs.google.com/spreadsheets/d/1y5syAENL/edit?gid=10',
    );

    // Must return null if synthesized or fallback URL equals currentUrl (ignoring #fragment) to prevent infinite redirect loops!
    expect(
      resolveAndroidIntentFallbackUrl(
        docsBannerIntent,
        currentUrl: 'https://docs.google.com/spreadsheets/d/1y5syAENL/edit?gid=10#gid=10',
      ),
      isNull,
    );
    expect(
      resolveAndroidIntentFallbackUrl(
        'intent://zalo.me/g/abc#Intent;scheme=zalo;S.browser_fallback_url=https%3A%2F%2Fzalo.me%2Fg%2Fabc;end',
        currentUrl: 'https://zalo.me/g/abc',
      ),
      isNull,
    );
  });

  test('resolveExternalAppNavigation plans native launch for zalo://, zoomus://, msteams://, mailto:, tel:, and intent:// while blocking viewerLocked and subframes', () {
    final zaloPlan = resolveExternalAppNavigation(
      'zalo://zaloapp.com/qr/g/nhom_hoc_tap',
      isForMainFrame: true,
      viewerLocked: false,
    );
    expect(zaloPlan, isNotNull);
    expect(zaloPlan!.scheme, 'zalo');
    expect(zaloPlan.launchUri, 'zalo://zaloapp.com/qr/g/nhom_hoc_tap');
    expect(zaloPlan.httpsFallbackUrl, isNull);

    final meetIntent =
        'intent://meet.app.goo.gl/?link=https://meet.google.com/abc-defg-hij&apn=com.google.android.apps.tachyon#Intent;'
        'package=com.google.android.gms;action=com.google.firebase.dynamiclinks.VIEW_DYNAMIC_LINK;scheme=https;'
        'S.browser_fallback_url=https%3A%2F%2Fplay.google.com%2Fstore%2Fapps%2Fdetails%3Fid%3Dcom.google.android.apps.tachyon;end;';
    final meetPlan = resolveExternalAppNavigation(
      meetIntent,
      isForMainFrame: true,
      viewerLocked: false,
      currentUrl: 'https://meet.google.com/abc-defg-hij',
    );
    expect(meetPlan, isNotNull);
    expect(meetPlan!.scheme, 'intent');
    expect(meetPlan.isStoreFallback, isTrue);
    expect(
      meetPlan.httpsFallbackUrl,
      'https://play.google.com/store/apps/details?id=com.google.android.apps.tachyon',
    );

    // Intent with market:// fallback AND scheme=https must keep isStoreFallback=false
    // so synthesized httpsFallbackUrl (e.g. docs.google.com) is NOT suppressed!
    const docsMarketAndHttpsIntent =
        'intent://docs.google.com/spreadsheets/d/1y5syAENL/edit?gid=10#Intent;'
        'S.browser_fallback_url=market%3A%2F%2Fdetails%3Fid%3Dcom.google.android.apps.docs.editors.sheets;'
        'package=com.google.android.apps.docs.editors.sheets;scheme=https;end';
    final docsPlan = resolveExternalAppNavigation(
      docsMarketAndHttpsIntent,
      isForMainFrame: true,
      viewerLocked: false,
      currentUrl: 'https://docs.google.com/spreadsheets/d/1y5syAENL/htmlview',
    );
    expect(docsPlan, isNotNull);
    expect(
      docsPlan!.marketFallbackUri,
      'market://details?id=com.google.android.apps.docs.editors.sheets',
    );
    expect(
      docsPlan.httpsFallbackUrl,
      'https://docs.google.com/spreadsheets/d/1y5syAENL/edit?gid=10',
    );
    expect(docsPlan.isStoreFallback, isFalse);

    for (final allowedSchemeUrl in [
      'zoomus://zoom.us/join?confno=123456789',
      'msteams://teams.microsoft.com/l/meetup-join/19',
      'mailto:teacher@ut.edu.vn',
      'tel:+842838992862',
      'market://details?id=com.zing.zalo',
    ]) {
      expect(
        resolveExternalAppNavigation(
          allowedSchemeUrl,
          isForMainFrame: true,
          viewerLocked: false,
        ),
        isNotNull,
        reason: allowedSchemeUrl,
      );
    }

    // Must block subframes, viewerLocked, standard web URLs, and unsafe/unknown schemes
    expect(
      resolveExternalAppNavigation(
        'zalo://zaloapp.com/qr/g/abc',
        isForMainFrame: false,
        viewerLocked: false,
      ),
      isNull,
    );
    expect(
      resolveExternalAppNavigation(
        'zalo://zaloapp.com/qr/g/abc',
        isForMainFrame: true,
        viewerLocked: true,
      ),
      isNull,
    );
    for (final nonExternal in [
      'https://docs.google.com/spreadsheets/d/1/edit',
      'http://127.0.0.1:51234/pdf-viewer/tok/pdf_host.html',
      'about:blank',
      'javascript:alert(1)',
      'data:text/html,<h1>x</h1>',
      'file:///sdcard/Download/a.pdf',
      'content://media/external/file/1',
      'oem-internal-debug://secret',
      'intent://127.0.0.1:54321/pdf-viewer/tok/#Intent;scheme=http;package=com.android.chrome;end',
      'intent://example.com/#Intent;scheme=http;end',
      'intent://pay#Intent;scheme=momo;end',
      'intent://example.com/#Intent;package=com.android.chrome;end',
      'intent://127.0.0.1/admin#Intent;scheme=https;end',
      'intent://user@docs.google.com/doc#Intent;scheme=https;end',
    ]) {
      expect(
        resolveExternalAppNavigation(
          nonExternal,
          isForMainFrame: true,
          viewerLocked: false,
        ),
        isNull,
        reason: nonExternal,
      );
    }

    // Demo Mode blockUthLms must cancel live *.ut.edu.vn navigations and intent:// handoffs
    expect(
      decideAppWebViewNavigation(
        'https://courses.ut.edu.vn/course/view.php?id=101',
        isForMainFrame: true,
        viewerLocked: false,
        blockUthLms: true,
      ).kind,
      AppNavigationKind.cancel,
    );
    expect(
      decideAppWebViewNavigation(
        'https://docs.google.com/spreadsheets/d/1/edit',
        isForMainFrame: true,
        viewerLocked: false,
        blockUthLms: true,
      ).kind,
      AppNavigationKind.allow,
    );
    expect(
      resolveExternalAppNavigation(
        'intent://courses.ut.edu.vn/my/#Intent;scheme=https;package=com.android.chrome;end',
        isForMainFrame: true,
        viewerLocked: false,
        blockUthLms: true,
      ),
      isNull,
    );
    // On iOS, Android-only intent:// URIs must NOT dispatch to launch_external_intent
    expect(
      resolveExternalAppNavigation(
        docsMarketAndHttpsIntent,
        isForMainFrame: true,
        viewerLocked: false,
        platform: TargetPlatform.iOS,
      ),
      isNull,
    );
    // Deferred popup sink bootstrap ignores transient empty / about:blank loads (TAB-POPUP-04)
    expect(isTransientPopupSinkBootstrapUrl(null), isTrue);
    expect(isTransientPopupSinkBootstrapUrl(''), isTrue);
    expect(isTransientPopupSinkBootstrapUrl('about:blank'), isTrue);
    expect(isTransientPopupSinkBootstrapUrl('about:blank#popup'), isTrue);
    expect(
      isTransientPopupSinkBootstrapUrl('https://docs.google.com/document/d/1/edit'),
      isFalse,
    );
  });

  test('parseAppWebViewColor, shouldRevealAppWebViewSurface, shouldReloadViaInitialUrl, and debug isInspectable work cleanly', () {
    expect(parseAppWebViewColor('#0F172A')?.toARGB32(), 0xFF0F172A);
    expect(parseAppWebViewColor('#F8FAFC')?.toARGB32(), 0xFFF8FAFC);
    expect(parseAppWebViewColor('#800F172A')?.toARGB32(), 0x800F172A);
    expect(parseAppWebViewColor(''), isNull);
    expect(parseAppWebViewColor('not-a-color'), isNull);

    // Surface cover stays over #000000 SurfaceView on empty/about:blank unless inline HTML was loaded
    expect(
      shouldRevealAppWebViewSurface(url: '', hasInitialHtml: false),
      isFalse,
    );
    expect(
      shouldRevealAppWebViewSurface(url: 'about:blank', hasInitialHtml: false),
      isFalse,
    );
    expect(
      shouldRevealAppWebViewSurface(url: 'about:blank', hasInitialHtml: true),
      isTrue,
    );
    expect(
      shouldRevealAppWebViewSurface(
        url: 'https://docs.google.com/spreadsheets/d/1/htmlview',
        hasInitialHtml: false,
      ),
      isTrue,
    );
    // Aborted uncommitted 302 redirect (e.g. zalo.me -> zalo://) must NOT reveal #000000 surface on spurious onLoadStop!
    expect(
      shouldRevealAppWebViewSurface(
        url: 'https://zalo.me/g/tbbrfqyyvys9qfe20z67',
        hasInitialHtml: false,
        suppressAbortedRedirectCommit: true,
      ),
      isFalse,
    );

    // Reload falls back to _initialUrl when uncommitted (null/empty/about:blank)
    expect(
      shouldReloadViaInitialUrl(
        activeControllerUrl: null,
        initialUrl: 'https://docs.google.com/spreadsheets/d/1/edit',
        hasInitialHtml: false,
      ),
      isTrue,
    );
    expect(
      shouldReloadViaInitialUrl(
        activeControllerUrl: 'about:blank',
        initialUrl: 'https://docs.google.com/spreadsheets/d/1/edit',
        hasInitialHtml: false,
      ),
      isTrue,
    );
    expect(
      shouldReloadViaInitialUrl(
        activeControllerUrl: 'https://docs.google.com/spreadsheets/d/1/htmlview',
        initialUrl: 'https://docs.google.com/spreadsheets/d/1/edit',
        hasInitialHtml: false,
      ),
      isFalse,
    );

    final debugBrowser = createAppWebViewSettings(
      viewerLocked: false,
      isInspectable: true,
    );
    expect(debugBrowser.isInspectable, isTrue);
    final releaseBrowser = createAppWebViewSettings(viewerLocked: false);
    expect(releaseBrowser.isInspectable, isFalse);

    // Pre-document HTTP 302 redirects (zalo.me -> zalo://, meet.google.com -> intent://)
    // must show external handoff/fallback card, whereas 200 OK HTML pages probing custom
    // schemes via inline JS (zoom.us/j/... -> zoomus://) must keep the live web page!
    expect(
      isUncommittedPreDocumentExternalRedirect(
        hasCommittedPage: false,
        hasDocumentTitle: false,
        isRedirect: true,
      ),
      isTrue,
    );
    expect(
      isUncommittedPreDocumentExternalRedirect(
        hasCommittedPage: false,
        hasDocumentTitle: false,
        isRedirect: false,
      ),
      isFalse,
    );
    expect(
      isUncommittedPreDocumentExternalRedirect(
        hasCommittedPage: false,
        hasDocumentTitle: true,
        isRedirect: true,
      ),
      isFalse,
    );
    expect(
      isUncommittedPreDocumentExternalRedirect(
        hasCommittedPage: true,
        hasDocumentTitle: false,
        isRedirect: true,
      ),
      isFalse,
    );
  });

  test('validateAppWebViewUserScriptOrigin and buildOriginPinnedAppUserScript enforce AT_DOCUMENT_START, forMainFrameOnly, exact origin match, and Demo/viewer blocks (BUG-134-01)', () {
    expect(
      validateAppWebViewUserScriptOrigin('https://portal.ut.edu.vn'),
      'https://portal.ut.edu.vn',
    );
    expect(
      validateAppWebViewUserScriptOrigin('  HTTPS://PORTAL.UT.EDU.VN/  '),
      'https://portal.ut.edu.vn',
    );
    for (final invalidOrigin in [
      null,
      '',
      '*',
      'http://portal.ut.edu.vn',
      'https://portal.ut.edu.vn:8443',
      'https://portal.ut.edu.vn/dashboard',
      'https://portal.ut.edu.vn?x=1',
      'https://portal.ut.edu.vn#frag',
      'https://user:pass@portal.ut.edu.vn',
      'https://127.0.0.1',
      'https://localhost',
      'https://192.168.1.1',
      'https://docs.google.com',
      'https://courses.ut.edu.vn',
      'https://thnn.ut.edu.vn',
      'javascript:alert(1)',
    ]) {
      expect(
        validateAppWebViewUserScriptOrigin(invalidOrigin),
        isNull,
        reason: '$invalidOrigin',
      );
    }

    const portalJs =
        'window.localStorage.setItem("account","eyJ.test");window.localStorage.setItem("role","sv");';
    final script = buildOriginPinnedAppUserScript(
      source: portalJs,
      allowedOrigin: 'https://portal.ut.edu.vn',
      targetUrl: 'https://portal.ut.edu.vn/dashboard',
      viewerLocked: false,
      blockUthLms: false,
    );
    expect(script, isNotNull);
    expect(script!.groupName, kAppWebViewAuthUserScriptGroup);
    expect(script.injectionTime, UserScriptInjectionTime.AT_DOCUMENT_START);
    expect(script.forMainFrameOnly, isTrue);
    expect(script.allowedOriginRules, {'https://portal.ut.edu.vn'});
    expect(script.source, contains('window.top !== window'));
    expect(
      script.source,
      contains('window.location.origin !== "https://portal.ut.edu.vn"'),
    );

    final initialScripts = buildInitialAppWebViewUserScripts(
      source: portalJs,
      allowedOrigin: 'https://portal.ut.edu.vn',
      initialUrl: 'https://portal.ut.edu.vn/dashboard',
      viewerLocked: false,
      blockUthLms: false,
    );
    expect(initialScripts, hasLength(1));

    // NEVER inject Portal UserScript on external docs.google.com, courses.ut.edu.vn, viewerLocked, or Demo Mode!
    for (final nonMatchingUrl in [
      'https://docs.google.com/document/d/1/edit',
      'https://courses.ut.edu.vn/my/',
      'https://thnn.ut.edu.vn/course/view.php?id=9',
      'http://127.0.0.1:51234/pdf-viewer/tok/pdf_host.html',
      'about:blank',
    ]) {
      expect(
        buildOriginPinnedAppUserScript(
          source: portalJs,
          allowedOrigin: 'https://portal.ut.edu.vn',
          targetUrl: nonMatchingUrl,
          viewerLocked: false,
          blockUthLms: false,
        ),
        isNull,
        reason: nonMatchingUrl,
      );
    }
    expect(
      buildOriginPinnedAppUserScript(
        source: portalJs,
        allowedOrigin: 'https://portal.ut.edu.vn',
        targetUrl: 'https://portal.ut.edu.vn/dashboard',
        viewerLocked: true,
        blockUthLms: false,
      ),
      isNull,
    );
    expect(
      buildOriginPinnedAppUserScript(
        source: portalJs,
        allowedOrigin: 'https://portal.ut.edu.vn',
        targetUrl: 'https://portal.ut.edu.vn/dashboard',
        viewerLocked: false,
        blockUthLms: true,
      ),
      isNull,
    );
  });

  test('applyAppWebViewAuthPayload serializes cookies + UserScript via MobileSessionQueue and supports cross-host UTH SSO without leaking to external hosts (BUG-134-01 / SEC-134-03)', () async {
    final queue = MobileSessionQueue();
    final hold = await queue.acquire(cancelled: Completer<void>().future);
    expect(hold, isNotNull);

    final ops = <String>[];
    // Started on Portal, user clicked SSO link to Courses -> self-heal applies Courses + sibling THNN cookies
    final portalInitial = Uri.parse('https://portal.ut.edu.vn/dashboard');
    final coursesTarget = Uri.parse('https://courses.ut.edu.vn/course/view.php?id=12');
    final future = applyAppWebViewAuthPayload(
      rawCookies: [
        {
          'name': 'MoodleSession',
          'value': 'courses_sess_fresh',
          'domain': 'courses.ut.edu.vn',
          'path': '/',
          'secure': true,
          'httpOnly': true,
        },
        {
          'name': 'MoodleSession',
          'value': 'thnn_sess_warm',
          'domain': 'thnn.ut.edu.vn',
          'path': '/',
          'secure': true,
          'httpOnly': true,
        },
        // Parent-domain cookie MUST be rejected even during UTH cross-host auth!
        {
          'name': 'MoodleSession',
          'value': 'bad_parent_domain',
          'domain': '.ut.edu.vn',
          'path': '/',
          'secure': true,
          'httpOnly': true,
        },
      ],
      rawInitScript: '',
      rawAllowedOrigin: '',
      targetUri: coursesTarget,
      initialUri: portalInitial,
      viewerLocked: false,
      blockUthLms: false,
      isDisposed: () => false,
      disposeSignal: Completer<void>().future,
      queue: queue,
      clearAuthUserScripts: () async {
        ops.add('clear_scripts');
      },
      addAuthUserScript: (script) async {
        ops.add('add_script:${script.allowedOriginRules.join(",")}');
      },
      deleteCookie: (url, name, path, domain) async {
        ops.add('del:${url.toString()}:$name:${domain ?? "host"}');
      },
      setCookie: (url, cookie) async {
        ops.add('set:${url.toString()}:${cookie.name}=${cookie.value}:${cookie.domain ?? "host"}');
      },
    );

    await Future<void>.delayed(const Duration(milliseconds: 20));
    expect(ops, isEmpty);

    hold!.release();
    final res = await future.timeout(const Duration(seconds: 2));
    expect(res.appliedCookies, 2);
    expect(res.installedScriptOrigin, isNull);
    expect(ops.first, 'clear_scripts');
    expect(
      ops,
      contains('set:https://courses.ut.edu.vn/:MoodleSession=courses_sess_fresh:host'),
    );
    expect(
      ops,
      contains('set:https://thnn.ut.edu.vn/:MoodleSession=thnn_sess_warm:host'),
    );
    expect(ops.any((item) => item.contains('bad_parent_domain')), isFalse);

    // Reverse direction: started on Courses, reactive recovery onto Portal registers AT_DOCUMENT_START UserScript
    ops.clear();
    final portalRes = await applyAppWebViewAuthPayload(
      rawCookies: const [],
      rawInitScript: 'window.localStorage.setItem("account","jwt.ok");',
      rawAllowedOrigin: 'https://portal.ut.edu.vn',
      targetUri: portalInitial,
      initialUri: coursesTarget,
      viewerLocked: false,
      blockUthLms: false,
      isDisposed: () => false,
      disposeSignal: Completer<void>().future,
      queue: queue,
      clearAuthUserScripts: () async => ops.add('clear_scripts'),
      addAuthUserScript: (s) async => ops.add('add_script:${s.allowedOriginRules.single}'),
      setCookie: (url, cookie) async => ops.add('set:${cookie.name}'),
    );
    expect(portalRes.appliedCookies, 0);
    expect(portalRes.installedScriptOrigin, 'https://portal.ut.edu.vn');
    expect(ops, ['clear_scripts', 'add_script:https://portal.ut.edu.vn']);

    // External non-UTH target must NEVER receive cookies or Portal UserScript!
    ops.clear();
    final externalRes = await applyAppWebViewAuthPayload(
      rawCookies: [
        {
          'name': 'MoodleSession',
          'value': 'steal',
          'domain': 'docs.google.com',
          'path': '/',
        },
      ],
      rawInitScript: 'window.localStorage.setItem("account","jwt.leak");',
      rawAllowedOrigin: 'https://portal.ut.edu.vn',
      targetUri: Uri.parse('https://docs.google.com/document/d/1/edit'),
      initialUri: portalInitial,
      viewerLocked: false,
      blockUthLms: false,
      isDisposed: () => false,
      disposeSignal: Completer<void>().future,
      queue: queue,
      clearAuthUserScripts: () async => ops.add('clear_scripts'),
      addAuthUserScript: (s) async => ops.add('add_script'),
      setCookie: (url, cookie) async => ops.add('set:${cookie.name}'),
    );
    expect(externalRes.appliedCookies, 0);
    expect(externalRes.installedScriptOrigin, isNull);
    expect(ops, ['clear_scripts']);
  });

  test('clearPersistedQuizSessions purges macOS WKWebsiteDataStore localStorage and cookies via MobileSessionQueue (SEC-134-01)', () async {
    final queue = MobileSessionQueue();
    final hold = await queue.acquire(cancelled: Completer<void>().future);
    final steps = <String>[];

    final cleanFuture = clearPersistedQuizSessions(
      platformOverride: TargetPlatform.macOS,
      sessionQueue: queue,
      deleteAllCookiesFn: () async {
        steps.add('delete_cookies');
      },
      purgeWebStorageFn: (platform) async {
        steps.add('purge_storage:${platform.name}');
      },
      clearAllCacheFn: () async {
        steps.add('clear_cache');
      },
    );

    await Future<void>.delayed(const Duration(milliseconds: 20));
    expect(steps, isEmpty);

    hold!.release();
    final ok = await cleanFuture.timeout(const Duration(seconds: 2));
    expect(ok, isTrue);
    expect(steps, [
      'delete_cookies',
      'purge_storage:macOS',
      'clear_cache',
    ]);
  });

  test('AppWebViewAuthScriptTracker preserves AT_DOCUMENT_START Portal UserScript across stale prior onLoadStop and clears on target onLoadStop or cross-origin nav (Challenge 4)', () {
    final tracker = AppWebViewAuthScriptTracker();
    expect(tracker.activeOrigin, isNull);
    expect(tracker.awaitingLoadStart, isFalse);

    // 1. User hits /login -> recovery arms AT_DOCUMENT_START Portal UserScript for /dashboard
    tracker.arm('https://portal.ut.edu.vn');
    expect(tracker.activeOrigin, 'https://portal.ut.edu.vn');
    expect(tracker.awaitingLoadStart, isTrue);

    // 2. Stale/aborted onLoadStop for prior /login arrives BEFORE /dashboard's onLoadStart -> MUST NOT clear!
    expect(
      tracker.shouldClearOnLoadStop(
        'https://portal.ut.edu.vn/login',
        hasInitialHtml: false,
      ),
      isFalse,
    );
    expect(tracker.activeOrigin, 'https://portal.ut.edu.vn');

    // 3. Target /dashboard main-frame onLoadStart arrives -> binds script to this load
    expect(
      tracker.onMainFrameLoadStart('https://portal.ut.edu.vn/dashboard'),
      AuthScriptLoadAction.keep,
    );
    expect(tracker.awaitingLoadStart, isFalse);

    // 4. Target /dashboard finishes (onLoadStop) -> clears one-shot UserScript!
    expect(
      tracker.shouldClearOnLoadStop(
        'https://portal.ut.edu.vn/dashboard',
        hasInitialHtml: false,
      ),
      isTrue,
    );
    tracker.clear();
    expect(tracker.activeOrigin, isNull);

    // 5. Cross-origin main-frame onLoadStart immediately signals clearNow even while awaitingLoadStart!
    tracker.arm('https://portal.ut.edu.vn');
    expect(
      tracker.onMainFrameLoadStart('https://courses.ut.edu.vn/my/'),
      AuthScriptLoadAction.clearNow,
    );
  });

  test('MOB-WEBVIEW-01: createAppWebViewSettings enables useOnRenderProcessGone and every InAppWebView mount site handles onRenderProcessGone safely', () {
    final browser = createAppWebViewSettings(viewerLocked: false);
    final viewer = createAppWebViewSettings(
      viewerLocked: true,
      viewerPrefix: 'http://127.0.0.1:51234/pdf-viewer/tok/',
    );
    final sink = createAppWebViewSettings(
      viewerLocked: true,
      javaScriptEnabled: false,
    );
    expect(browser.useOnRenderProcessGone, isTrue);
    expect(viewer.useOnRenderProcessGone, isTrue);
    expect(sink.useOnRenderProcessGone, isTrue);

    expect(
      formatAppWebViewRenderProcessGoneError(true),
      'render_process_gone:crashed',
    );
    expect(
      formatAppWebViewRenderProcessGoneError(false),
      'render_process_gone:terminated',
    );
    expect(
      formatAppWebViewRenderProcessGoneError(null),
      'render_process_gone:terminated',
    );

    // Every InAppWebView( mount site in app_webview_control.dart (offstage popup sink + main WebView)
    // must define a non-null onRenderProcessGone callback so Android WebViewClient returns true
    // instead of terminating the host app process.
    final source = File('lib/src/app_webview_control.dart').readAsStringSync();
    final mountArgs = _extractInAppWebViewCallArguments(source);
    expect(mountArgs, hasLength(2));
    for (final callBody in mountArgs) {
      expect(
        callBody,
        contains('onRenderProcessGone:'),
        reason: 'Missing onRenderProcessGone on InAppWebView mount site:\n$callBody',
      );
      expect(
        callBody,
        isNot(contains('onRenderProcessGone: null')),
      );
    }

    // Active main WebView handler: mounted -> clears dead state and emits structured web_error.
    var cleared = 0;
    final errors = <String>[];
    handleAppWebViewRenderProcessGone(
      disposed: false,
      mounted: true,
      detail: RenderProcessGoneDetail(didCrash: true),
      clearDeadWebViewState: () => cleared++,
      reportWebError: errors.add,
    );
    expect(cleared, 1);
    expect(errors, ['render_process_gone:crashed']);

    handleAppWebViewRenderProcessGone(
      disposed: false,
      mounted: true,
      detail: RenderProcessGoneDetail(didCrash: false),
      clearDeadWebViewState: () => cleared++,
      reportWebError: errors.add,
    );
    expect(cleared, 2);
    expect(errors, [
      'render_process_gone:crashed',
      'render_process_gone:terminated',
    ]);

    // Unmounted or disposed -> safe no-op without throwing or emitting errors.
    handleAppWebViewRenderProcessGone(
      disposed: false,
      mounted: false,
      detail: RenderProcessGoneDetail(didCrash: true),
      clearDeadWebViewState: () => cleared++,
      reportWebError: errors.add,
    );
    handleAppWebViewRenderProcessGone(
      disposed: true,
      mounted: true,
      detail: RenderProcessGoneDetail(didCrash: true),
      clearDeadWebViewState: () => cleared++,
      reportWebError: errors.add,
    );
    expect(cleared, 2);
    expect(errors, hasLength(2));

    // Offstage popup sink handler: swallows safely and removes sink only when mounted and alive.
    var removedSinks = 0;
    handleAppWebViewPopupSinkRenderProcessGone(
      disposed: false,
      mounted: true,
      removeSink: () => removedSinks++,
    );
    handleAppWebViewPopupSinkRenderProcessGone(
      disposed: false,
      mounted: false,
      removeSink: () => removedSinks++,
    );
    handleAppWebViewPopupSinkRenderProcessGone(
      disposed: true,
      mounted: true,
      removeSink: () => removedSinks++,
    );
    expect(removedSinks, 1);
  });
}

List<String> _extractInAppWebViewCallArguments(String source) {
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
