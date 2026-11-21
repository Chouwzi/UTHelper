import 'package:flutter/foundation.dart';

enum QuizPopupRequestKind {
  blank,
  sameOrigin,
  external,
  invalid,
  limitReached,
}

const maxQuizPopupCount = 4;

final _controlOrBackslashPattern = RegExp(r'[\\\x00-\x1f\x7f]');
final _rawAuthorityPattern = RegExp(
  r'^[a-z][a-z0-9+.-]*://([^/?#]+)',
  caseSensitive: false,
);

bool hasUnsafeUrlCharacters(String value) =>
    _controlOrBackslashPattern.hasMatch(value);

/// Returns true when [value]'s raw authority includes userinfo or an explicit
/// `:port` (including default `:443`, which Dart `Uri` canonicalizes away).
bool hasExplicitPortOrUserInfo(String value) {
  final rawAuthority = _rawAuthorityPattern.firstMatch(value)?.group(1);
  if (rawAuthority == null || rawAuthority.isEmpty) return true;
  return rawAuthority.contains('@') || rawAuthority.contains(':');
}

/// On Windows and iOS, returning `false` from `onCreateWindow` falls back to
/// loading the popup URL in the main WebView. Adopt blocked popups into an
/// offstage cancelling sink instead so the main quiz frame never navigates.
bool shouldAdoptBlockedPopupToCancel(TargetPlatform platform) =>
    platform == TargetPlatform.windows || platform == TargetPlatform.iOS;

bool allowsQuizSubframeNavigation(String? rawUrl) {
  if (rawUrl == null ||
      rawUrl.isEmpty ||
      hasUnsafeUrlCharacters(rawUrl) ||
      rawUrl.contains(RegExp(r'\s'))) {
    return false;
  }

  final target = Uri.tryParse(rawUrl);
  if (target == null) return false;

  final scheme = target.scheme.toLowerCase();
  if (scheme == 'about') {
    return target.host.isEmpty && target.path.toLowerCase() == 'blank';
  }
  return scheme == 'https' &&
      target.hasAuthority &&
      target.host.isNotEmpty &&
      target.userInfo.isEmpty;
}

QuizPopupRequestKind classifyQuizPopupRequest(
  String? rawUrl,
  Uri? trustedOrigin, {
  required int activePopupCount,
}) {
  if (activePopupCount >= maxQuizPopupCount) {
    return QuizPopupRequestKind.limitReached;
  }
  if (rawUrl == null ||
      rawUrl.isEmpty ||
      rawUrl.toLowerCase() == 'about:blank') {
    return QuizPopupRequestKind.blank;
  }
  if (hasUnsafeUrlCharacters(rawUrl)) {
    return QuizPopupRequestKind.invalid;
  }

  final target = Uri.tryParse(rawUrl);
  if (target == null ||
      !target.isAbsolute ||
      !target.hasAuthority ||
      target.host.isEmpty ||
      target.userInfo.isNotEmpty ||
      target.hasPort ||
      hasExplicitPortOrUserInfo(rawUrl) ||
      trustedOrigin == null ||
      !trustedOrigin.isAbsolute ||
      !trustedOrigin.hasAuthority ||
      trustedOrigin.host.isEmpty ||
      trustedOrigin.userInfo.isNotEmpty ||
      trustedOrigin.hasPort) {
    return QuizPopupRequestKind.invalid;
  }

  if (trustedOrigin.scheme.toLowerCase() == 'https' &&
      target.scheme.toLowerCase() != 'https') {
    return QuizPopupRequestKind.invalid;
  }

  final sameOrigin =
      target.scheme.toLowerCase() == trustedOrigin.scheme.toLowerCase() &&
          target.host.toLowerCase() == trustedOrigin.host.toLowerCase() &&
          target.port == trustedOrigin.port;
  return sameOrigin
      ? QuizPopupRequestKind.sameOrigin
      : QuizPopupRequestKind.external;
}
