import 'dart:async';

import 'package:flet/flet.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_inappwebview/flutter_inappwebview.dart';
import 'package:path/path.dart' as path;
import 'package:path_provider/path_provider.dart';

import 'mobile_session_queue.dart';
import 'quiz_profile_storage.dart';
import 'quiz_web_storage.dart';

const _cleanerTimeout = Duration(seconds: 5);
final _sessionKeyPattern = RegExp(r'^[0-9a-fA-F]{64}$');

/// Purges persisted quiz WebView profiles (Windows) and shared WebView stores
/// (Android/iOS) when the user logs out or switches accounts.
class UthQuizSessionCleanerService extends FletService {
  UthQuizSessionCleanerService({
    required super.control,
    Set<String> activeWindowsProfilePaths = const <String>{},
  }) : _activeWindowsProfilePaths = activeWindowsProfilePaths;

  final Set<String> _activeWindowsProfilePaths;
  bool _disposed = false;
  final Completer<void> _disposeSignal = Completer<void>();

  @override
  void init() {
    super.init();
    control.addInvokeMethodListener(_invokeMethod);
  }

  Future<dynamic> _invokeMethod(String name, dynamic args) async {
    if (_disposed) return false;
    if (name == 'clear_sessions') {
      return await clearPersistedQuizSessions(
        activeWindowsProfilePaths: _activeWindowsProfilePaths,
        cancelled: _disposeSignal.future,
      );
    }
    if (name == 'prewarm') {
      final sessionKey = args is Map ? args['session_key'] : null;
      return await prewarmQuizWebView(
        sessionKey: sessionKey is String ? sessionKey : null,
      );
    }
    return false;
  }

  @override
  void dispose() {
    _disposed = true;
    if (!_disposeSignal.isCompleted) _disposeSignal.complete();
    control.removeInvokeMethodListener(_invokeMethod);
    super.dispose();
  }
}

Future<bool> prewarmQuizWebView({
  required String? sessionKey,
  Future<String?> Function()? getWindowsRuntimeVersion,
  Future<Directory> Function()? getSupportDirectory,
  Future<WebViewEnvironment> Function(Directory profileDir)?
      createWindowsEnvironment,
  Future<void> Function(WebViewEnvironment environment)?
      disposeWindowsEnvironment,
  QuizWindowsEnvironmentCache<WebViewEnvironment>? windowsCache,
}) async {
  if (sessionKey == null || !_sessionKeyPattern.hasMatch(sessionKey)) {
    return false;
  }
  if (kIsWeb) return true;
  if (defaultTargetPlatform == TargetPlatform.windows) {
    try {
      final runtimeVersion = await (getWindowsRuntimeVersion != null
          ? getWindowsRuntimeVersion()
          : WebViewEnvironment.getAvailableVersion());
      if (runtimeVersion == null || runtimeVersion.isEmpty) {
        return false;
      }
      final supportDirectory = await (getSupportDirectory != null
          ? getSupportDirectory()
          : getApplicationSupportDirectory());
      final profilesRoot = Directory(
        path.join(supportDirectory.path, 'uthhelper', 'quiz-webview'),
      );
      final cache = windowsCache ?? sharedQuizWindowsEnvironmentCache;
      await cache.acquire(
        sessionKey,
        profilesRoot,
        createEnvironment: createWindowsEnvironment ??
            (profileDir) => WebViewEnvironment.create(
                  settings: WebViewEnvironmentSettings(
                    userDataFolder: profileDir.path,
                  ),
                ),
        disposeEnvironment:
            disposeWindowsEnvironment ?? (environment) => environment.dispose(),
        markActiveMount: false,
        sweepStale: true,
      );
      return true;
    } catch (_) {
      return false;
    }
  }
  if (defaultTargetPlatform == TargetPlatform.android ||
      defaultTargetPlatform == TargetPlatform.iOS) {
    try {
      CookieManager.instance();
      await InAppWebViewController.getDefaultUserAgent()
          .timeout(const Duration(seconds: 3));
      return true;
    } catch (_) {
      return false;
    }
  }
  return true;
}

Future<bool> clearPersistedQuizSessions({
  Set<String> activeWindowsProfilePaths = const <String>{},
  Future<void>? cancelled,
}) async {
  if (kIsWeb) return true;
  if (defaultTargetPlatform == TargetPlatform.windows) {
    try {
      await disposeCachedQuizWebViewEnvironments();
      final supportDirectory = await getApplicationSupportDirectory();
      final profilesRoot = Directory(
        path.join(supportDirectory.path, 'uthhelper', 'quiz-webview'),
      );
      await sweepQuizProfileDirectories(
        profilesRoot,
        activePaths: activeWindowsProfilePaths,
      );
      return true;
    } catch (_) {
      return false;
    }
  }
  if (defaultTargetPlatform == TargetPlatform.android ||
      defaultTargetPlatform == TargetPlatform.iOS) {
    final cancelSignal = cancelled ?? Completer<void>().future;
    final timeout = Completer<void>();
    final timer = Timer(_cleanerTimeout, timeout.complete);
    MobileSessionLease? lease;
    try {
      lease = await sharedMobileSessionQueue.acquire(
        cancelled: Future.any<void>([cancelSignal, timeout.future]),
      );
      if (lease == null) return false;
      await CookieManager.instance()
          .deleteAllCookies()
          .timeout(_cleanerTimeout);
      await purgeQuizWebStorage(
        defaultTargetPlatform,
        WebStorageManager.instance(),
      ).timeout(_cleanerTimeout);
      await InAppWebViewController.clearAllCache().timeout(_cleanerTimeout);
      return true;
    } catch (_) {
      return false;
    } finally {
      timer.cancel();
      lease?.release();
    }
  }
  return true;
}
