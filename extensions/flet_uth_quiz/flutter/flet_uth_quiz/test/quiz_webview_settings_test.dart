import 'package:flet_uth_quiz/src/quiz_webview_settings.dart';
import 'package:flutter_inappwebview/flutter_inappwebview.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('retains quiz JavaScript and windows with local access disabled', () {
    final settings = createQuizWebViewSettings();

    expect(settings.javaScriptEnabled, isTrue);
    expect(settings.javaScriptCanOpenWindowsAutomatically, isTrue);
    expect(settings.supportMultipleWindows, isTrue);
    expect(settings.useShouldOverrideUrlLoading, isTrue);
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
  });
}
