import 'package:flutter_inappwebview/flutter_inappwebview.dart';

InAppWebViewSettings createQuizWebViewSettings() {
  return InAppWebViewSettings(
    javaScriptEnabled: true,
    javaScriptCanOpenWindowsAutomatically: true,
    supportMultipleWindows: true,
    useShouldOverrideUrlLoading: true,
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
  );
}
