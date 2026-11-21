enum QuizPopupRequestKind {
  blank,
  sameOrigin,
  external,
  invalid,
  limitReached,
}

const maxQuizPopupCount = 4;

bool allowsQuizSubframeNavigation(String? rawUrl) {
  if (rawUrl == null || rawUrl.isEmpty) return false;

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

  final target = Uri.tryParse(rawUrl);
  if (target == null ||
      !target.isAbsolute ||
      !target.hasAuthority ||
      target.host.isEmpty ||
      target.userInfo.isNotEmpty ||
      trustedOrigin == null) {
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
