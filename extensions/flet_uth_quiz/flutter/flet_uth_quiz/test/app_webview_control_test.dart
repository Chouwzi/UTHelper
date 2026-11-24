import 'package:flutter_inappwebview/flutter_inappwebview.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:flet_uth_quiz/src/app_webview_settings.dart';

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
    expect(browser.userAgent, 'Mozilla/5.0 Chrome/131.0 Mobile Safari/537.36');

    final viewer = createAppWebViewSettings(viewerLocked: true);
    expect(viewer.javaScriptEnabled, isTrue);
    expect(viewer.thirdPartyCookiesEnabled, isFalse);
    expect(viewer.supportMultipleWindows, isFalse);
    expect(viewer.javaScriptCanOpenWindowsAutomatically, isFalse);
    expect(viewer.cacheEnabled, isFalse);
    expect(viewer.cacheMode, CacheMode.LOAD_NO_CACHE);
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

    // Blocks unsafe schemes and unapproved HTTP/loopback ports
    for (final blocked in [
      'javascript:alert(1)',
      'file:///etc/hosts',
      'data:text/html,<h1>x</h1>',
      'http://evil.example/phish',
      'http://127.0.0.1:44444/other',
      'https://user:pass@courses.ut.edu.vn/',
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
  });

  test('validateAppWebViewCookie normalizes MoodleSession cookies and rejects cross-site injection', () {
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

    final subdomain = validateAppWebViewCookie(
      {
        'name': 'MoodleID1_',
        'value': 'val1',
        'domain': '.ut.edu.vn',
        'path': '/',
        'secure': true,
        'http_only': false,
      },
      target,
    );
    expect(subdomain, isNotNull);
    expect(subdomain!.domain, 'ut.edu.vn');

    expect(
      validateAppWebViewCookie(
        {
          'name': 'MoodleSession',
          'value': 'steal',
          'domain': 'evil.example',
          'path': '/',
          'secure': true,
        },
        target,
      ),
      isNull,
    );
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
