import 'package:flet_uth_quiz/src/quiz_webview_settings.dart';
import 'package:flutter_inappwebview/flutter_inappwebview.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('retains quiz JavaScript and windows with local access and DevTools disabled', () {
    final settings = createQuizWebViewSettings();

    expect(settings.javaScriptEnabled, isTrue);
    expect(settings.javaScriptCanOpenWindowsAutomatically, isTrue);
    expect(settings.supportMultipleWindows, isTrue);
    expect(settings.useShouldOverrideUrlLoading, isTrue);
    expect(settings.regexToCancelSubFramesLoading, quizSubframeCancelRegex);
    expect(settings.isInspectable, isFalse);
    expect(settings.allowContentAccess, isFalse);
    expect(settings.allowFileAccess, isFalse);
    expect(settings.allowFileAccessFromFileURLs, isFalse);
    expect(settings.allowUniversalAccessFromFileURLs, isFalse);
    expect(settings.geolocationEnabled, isFalse);
    expect(
      settings.mixedContentMode,
      MixedContentMode.MIXED_CONTENT_NEVER_ALLOW,
    );
    expect(settings.incognito, isFalse);
    expect(settings.saveFormData, isFalse);
    expect(settings.useHybridComposition, isTrue);
    expect(settings.cacheEnabled, isTrue);
    expect(settings.cacheMode, CacheMode.LOAD_DEFAULT);
    expect(settings.hardwareAcceleration, isTrue);
    expect(settings.domStorageEnabled, isTrue);
    expect(settings.databaseEnabled, isTrue);
    expect(settings.offscreenPreRaster, isFalse);
    expect(settings.useOnRenderProcessGone, isTrue);
  });

  test('can build sink settings with popups disabled', () {
    final settings = createQuizWebViewSettings(allowPopups: false);

    expect(settings.javaScriptCanOpenWindowsAutomatically, isFalse);
    expect(settings.supportMultipleWindows, isFalse);
    expect(settings.isInspectable, isFalse);
    expect(settings.useOnRenderProcessGone, isTrue);
    expect(settings.regexToCancelSubFramesLoading, quizSubframeCancelRegex);
  });

  test('Android subframe cancellation regex allows HTTPS and about:blank only', () {
    expect(quizSubframeCancelRegex.startsWith('(?is)'), isTrue);
    final dartEquivalent = RegExp(
      quizSubframeCancelRegex.substring('(?is)'.length),
      caseSensitive: false,
      dotAll: true,
    );

    for (final allowed in [
      'https://courses.ut.edu.vn/pluginfile.php/quiz-image.png',
      'HTTPS://MEDIA.EXAMPLE.ORG/video.mp4?token=1#t=2',
      'https://courses.ut.edu.vn',
      'about:blank',
      'ABOUT:BLANK#question-frame',
      'about:blank?x=1',
    ]) {
      expect(
        dartEquivalent.hasMatch(allowed),
        isFalse,
        reason: 'expected subframe to be allowed: $allowed',
      );
    }

    for (final cancelled in [
      '',
      'http://media.example.org/insecure-video.mp4',
      'javascript:alert(1)',
      'file:///private/data',
      'content://provider/item',
      'intent://scan/#Intent;end',
      'data:text/html,<script>alert(1)</script>',
      'blob:https://courses.ut.edu.vn/id',
      'ftp://example.org/file',
      'https://student@media.example.org/file',
      'https:///missing-host',
      r'https://courses.ut.edu.vn\evil',
      r'https://courses.ut.edu.vn/\evil',
      'https://courses.ut.edu.vn\nhttp://evil',
      'https://courses.ut.edu.vn/\nhttp://evil.com',
      'https://courses.ut.edu.vn/\rhttp://evil.com',
      'https://courses.ut.edu.vn/\x00evil',
      'https://courses.ut.edu.vn\u0085evil.com/',
      'about:blank\n',
      'about:blank#\n',
      r'about:blank?\evil',
      'about:settings',
    ]) {
      expect(
        dartEquivalent.hasMatch(cancelled),
        isTrue,
        reason: 'expected subframe to be cancelled: $cancelled',
      );
    }
  });
}
