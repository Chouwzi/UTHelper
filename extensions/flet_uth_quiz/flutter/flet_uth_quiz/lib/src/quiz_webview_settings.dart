import 'package:flutter_inappwebview/flutter_inappwebview.dart';

/// Cancels any non-HTTPS and non-`about:blank` subframe URL natively on Android.
///
/// Android's `InAppWebViewClient.shouldOverrideUrlLoading` ignores Dart's
/// `NavigationActionPolicy.CANCEL` on subframes unless this Java `Pattern`
/// matches the target URL.
const quizSubframeCancelRegex =
    r'(?is)^(?!(?:https://[^\x00-\x20\x7f-\x9f/@?#\\]+(?:[/?#][^\x00-\x1f\x7f\\]*)?|about:blank(?:[?#][^\x00-\x1f\x7f\\]*)?)(?![\s\S])).*';

InAppWebViewSettings createQuizWebViewSettings({bool allowPopups = true}) {
  return InAppWebViewSettings(
    javaScriptEnabled: true,
    javaScriptCanOpenWindowsAutomatically: allowPopups,
    supportMultipleWindows: allowPopups,
    useShouldOverrideUrlLoading: true,
    regexToCancelSubFramesLoading: quizSubframeCancelRegex,
    isInspectable: false,
    allowContentAccess: false,
    allowFileAccess: false,
    allowFileAccessFromFileURLs: false,
    allowUniversalAccessFromFileURLs: false,
    geolocationEnabled: false,
    mixedContentMode: MixedContentMode.MIXED_CONTENT_NEVER_ALLOW,
    // Cookie stores are explicitly cleared and serialized on mobile before
    // bootstrap. Incognito would clear Android cookies on WebView creation
    // and use a non-persistent iOS store separate from CookieManager.
    incognito: false,
    saveFormData: false,
    useHybridComposition: true,
    cacheEnabled: true,
    cacheMode: CacheMode.LOAD_DEFAULT,
    hardwareAcceleration: true,
    domStorageEnabled: true,
    databaseEnabled: true,
    offscreenPreRaster: false,
  );
}
