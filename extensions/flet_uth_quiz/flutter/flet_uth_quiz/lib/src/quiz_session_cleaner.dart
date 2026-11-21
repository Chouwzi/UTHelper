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
    if (_disposed || name != 'clear_sessions') return false;
    return await clearPersistedQuizSessions(
      activeWindowsProfilePaths: _activeWindowsProfilePaths,
      cancelled: _disposeSignal.future,
    );
  }

  @override
  void dispose() {
    _disposed = true;
    if (!_disposeSignal.isCompleted) _disposeSignal.complete();
    control.removeInvokeMethodListener(_invokeMethod);
    super.dispose();
  }
}

Future<bool> clearPersistedQuizSessions({
  Set<String> activeWindowsProfilePaths = const <String>{},
  Future<void>? cancelled,
}) async {
  if (kIsWeb) return true;
  if (defaultTargetPlatform == TargetPlatform.windows) {
    try {
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
