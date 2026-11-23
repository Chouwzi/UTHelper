import 'dart:async';
import 'dart:collection';
import 'dart:io';

import 'package:flutter_inappwebview/flutter_inappwebview.dart';
import 'package:path/path.dart' as path;

final Set<String> activeQuizWindowsProfilePaths = <String>{};

class QuizWindowsEnvironmentEntry<T> {
  QuizWindowsEnvironmentEntry({
    required this.sessionKey,
    required this.profileDirectory,
    required this.environmentFuture,
    this.activeMountCount = 0,
    this.sessionEpoch = 0,
  });

  final String sessionKey;
  final Directory profileDirectory;
  final Future<T> environmentFuture;
  T? environment;
  int activeMountCount;
  int sessionEpoch;
  Future<void>? pendingCloseCleanup;
}

/// Bounded LRU cache of Windows WebView2 environments keyed by quiz `sessionKey`.
///
/// Reusing `WebViewEnvironment` + `profileDirectory` across quiz opens avoids
/// cold WebView2 startup and preserves the Edge HTTP disk cache & V8 code
/// cache while quiz cookies/DOM storage are cleared per attempt.
class QuizWindowsEnvironmentCache<T> {
  QuizWindowsEnvironmentCache({
    this.maxCachedEnvironments = 2,
    Set<String>? activeProfilePaths,
  }) : activeProfilePaths = activeProfilePaths ?? activeQuizWindowsProfilePaths;

  final int maxCachedEnvironments;
  final Set<String> activeProfilePaths;
  final LinkedHashMap<String, QuizWindowsEnvironmentEntry<T>> _entries =
      LinkedHashMap<String, QuizWindowsEnvironmentEntry<T>>();
  final Map<String, Future<void>> _pendingCloseCleanups =
      <String, Future<void>>{};
  final Map<String, int> _disposingPathCounts = <String, int>{};

  bool get isEmpty => _entries.isEmpty;
  int get length => _entries.length;

  bool containsKey(String sessionKey) =>
      _entries.containsKey(sessionKey.toLowerCase());

  int sessionEpochFor(String sessionKey) =>
      _entries[sessionKey.toLowerCase()]?.sessionEpoch ?? 0;

  int activeMountCountFor(String sessionKey) =>
      _entries[sessionKey.toLowerCase()]?.activeMountCount ?? 0;

  int bumpSessionEpoch(String sessionKey) {
    final entry = _entries[sessionKey.toLowerCase()];
    if (entry == null) return 0;
    entry.sessionEpoch += 1;
    return entry.sessionEpoch;
  }

  bool shouldRunCloseCleanup(
    String sessionKey, {
    required int expectedEpoch,
  }) {
    final entry = _entries[sessionKey.toLowerCase()];
    if (entry == null) return false;
    return entry.activeMountCount == 0 && entry.sessionEpoch == expectedEpoch;
  }

  Future<void> waitForCloseCleanup(String sessionKey) async {
    final pending = _pendingCloseCleanups[sessionKey.toLowerCase()];
    if (pending != null) {
      try {
        await pending;
      } catch (_) {}
    }
  }

  Future<void> runCloseCleanup(
    String sessionKey, {
    required int expectedEpoch,
    Future<void> Function()? beforeCleanup,
    required Future<void> Function() cleanup,
  }) async {
    final normalizedKey = sessionKey.toLowerCase();
    final entry = _entries[normalizedKey];
    if (entry == null ||
        entry.activeMountCount > 0 ||
        entry.sessionEpoch != expectedEpoch) {
      return;
    }
    final previousCleanup = _pendingCloseCleanups[normalizedKey];
    late final Future<void> currentCleanup;
    currentCleanup = () async {
      if (previousCleanup != null) {
        try {
          await previousCleanup;
        } catch (_) {}
      }
      if (beforeCleanup != null) {
        try {
          await beforeCleanup();
        } catch (_) {}
      }
      final currentEntry = _entries[normalizedKey];
      if (!identical(currentEntry, entry) ||
          entry.activeMountCount > 0 ||
          entry.sessionEpoch != expectedEpoch) {
        return;
      }
      await cleanup();
    }();
    _pendingCloseCleanups[normalizedKey] = currentCleanup;
    entry.pendingCloseCleanup = currentCleanup;
    try {
      await currentCleanup;
    } finally {
      if (identical(_pendingCloseCleanups[normalizedKey], currentCleanup)) {
        _pendingCloseCleanups.remove(normalizedKey);
      }
      if (identical(entry.pendingCloseCleanup, currentCleanup)) {
        entry.pendingCloseCleanup = null;
      }
    }
  }

  bool _isPathStillOwned(String normalizedPath) {
    if ((_disposingPathCounts[normalizedPath] ?? 0) > 0) {
      return true;
    }
    return _entries.values.any(
      (entry) =>
          normalizeProfilePath(entry.profileDirectory.path) == normalizedPath,
    );
  }

  void _removeProfilePathIfUnowned(String normalizedPath) {
    if (!_isPathStillOwned(normalizedPath)) {
      activeProfilePaths.remove(normalizedPath);
    }
  }

  Future<({T environment, Directory profileDirectory, int sessionEpoch})>
      acquire(
    String sessionKey,
    Directory profilesRoot, {
    required Future<T> Function(Directory profileDirectory) createEnvironment,
    Future<void> Function(T environment)? disposeEnvironment,
    bool markActiveMount = false,
    bool sweepStale = false,
  }) async {
    final normalizedKey = sessionKey.toLowerCase();
    var entry = _entries.remove(normalizedKey);
    var isNewEntry = false;
    if (entry != null) {
      _entries[normalizedKey] = entry;
      if (markActiveMount) {
        entry.activeMountCount += 1;
        entry.sessionEpoch += 1;
      }
    } else {
      isNewEntry = true;
      final profileDirectory = Directory(
        path.join(profilesRoot.path, normalizedKey),
      );
      final normalizedPath = normalizeProfilePath(profileDirectory.path);
      activeProfilePaths.add(normalizedPath);

      final createdFuture = () async {
        await profilesRoot.create(recursive: true);
        await profileDirectory.create(recursive: true);
        return await createEnvironment(profileDirectory);
      }();
      entry = QuizWindowsEnvironmentEntry<T>(
        sessionKey: normalizedKey,
        profileDirectory: profileDirectory,
        environmentFuture: createdFuture,
        activeMountCount: markActiveMount ? 1 : 0,
        sessionEpoch: markActiveMount ? 1 : 0,
      );
      _entries[normalizedKey] = entry;

      if (sweepStale) {
        unawaited(
          sweepQuizProfileDirectories(
            profilesRoot,
            activePaths: activeProfilePaths,
          ),
        );
      }
    }

    final mountEpoch = entry.sessionEpoch;
    try {
      final env = await entry.environmentFuture;
      entry.environment = env;
      if (markActiveMount) {
        await waitForCloseCleanup(normalizedKey);
      }
      if (isNewEntry && identical(_entries[normalizedKey], entry)) {
        await _evictOverflow(disposeEnvironment: disposeEnvironment);
      }
      return (
        environment: env,
        profileDirectory: entry.profileDirectory,
        sessionEpoch: mountEpoch,
      );
    } catch (_) {
      if (markActiveMount && entry.activeMountCount > 0) {
        entry.activeMountCount -= 1;
      }
      if (identical(_entries[normalizedKey], entry)) {
        _entries.remove(normalizedKey);
        _removeProfilePathIfUnowned(
          normalizeProfilePath(entry.profileDirectory.path),
        );
      }
      rethrow;
    }
  }

  void releaseMount(String sessionKey) {
    final entry = _entries[sessionKey.toLowerCase()];
    if (entry != null && entry.activeMountCount > 0) {
      entry.activeMountCount -= 1;
    }
  }

  Future<void> _evictOverflow({
    Future<void> Function(T environment)? disposeEnvironment,
  }) async {
    if (_entries.length <= maxCachedEnvironments) return;
    final candidates = _entries.keys
        .where((key) => _entries[key]!.activeMountCount == 0)
        .toList(growable: false);
    for (final key in candidates) {
      if (_entries.length <= maxCachedEnvironments) break;
      final evicted = _entries.remove(key);
      if (evicted == null) continue;
      await _disposeEntry(evicted, disposeEnvironment: disposeEnvironment);
    }
  }

  Future<void> disposeInactive({
    Future<void> Function(T environment)? disposeEnvironment,
  }) async {
    final inactiveKeys = _entries.keys
        .where((key) => _entries[key]!.activeMountCount == 0)
        .toList(growable: false);
    for (final key in inactiveKeys) {
      final entry = _entries.remove(key);
      if (entry == null) continue;
      await _disposeEntry(entry, disposeEnvironment: disposeEnvironment);
    }
  }

  Future<void> _disposeEntry(
    QuizWindowsEnvironmentEntry<T> entry, {
    Future<void> Function(T environment)? disposeEnvironment,
  }) async {
    final normalizedPath = normalizeProfilePath(entry.profileDirectory.path);
    _disposingPathCounts[normalizedPath] =
        (_disposingPathCounts[normalizedPath] ?? 0) + 1;
    var disposed = false;
    try {
      final pendingCleanup = entry.pendingCloseCleanup;
      if (pendingCleanup != null) {
        try {
          await pendingCleanup;
        } catch (_) {}
      }
      T? env = entry.environment;
      if (env == null) {
        try {
          env = await entry.environmentFuture;
        } catch (_) {
          env = null;
        }
      }
      if (env != null && disposeEnvironment != null) {
        await disposeEnvironment(env);
      }
      disposed = true;
    } catch (_) {
      disposed = false;
    } finally {
      final remaining = (_disposingPathCounts[normalizedPath] ?? 1) - 1;
      if (remaining <= 0) {
        _disposingPathCounts.remove(normalizedPath);
      } else {
        _disposingPathCounts[normalizedPath] = remaining;
      }
      if (disposed) {
        _removeProfilePathIfUnowned(normalizedPath);
      }
    }
  }
}

final QuizWindowsEnvironmentCache<WebViewEnvironment>
    sharedQuizWindowsEnvironmentCache =
    QuizWindowsEnvironmentCache<WebViewEnvironment>();

Future<void> disposeCachedQuizWebViewEnvironments() async {
  await sharedQuizWindowsEnvironmentCache.disposeInactive(
    disposeEnvironment: (environment) => environment.dispose(),
  );
}

const quizProfileDeleteRetryDelays = <Duration>[
  Duration(milliseconds: 100),
  Duration(milliseconds: 250),
  Duration(milliseconds: 500),
];

int _profileFallbackCounter = 0;

String buildFallbackQuizProfileDirectoryName(
  String sessionKey, {
  int? microsecondsSinceEpoch,
}) {
  _profileFallbackCounter += 1;
  final micros =
      microsecondsSinceEpoch ?? DateTime.now().microsecondsSinceEpoch;
  return '${sessionKey.toLowerCase()}-$micros-$_profileFallbackCounter';
}

String normalizeProfilePath(String rawPath) {
  final normalized = path.normalize(rawPath);
  return Platform.isWindows ? normalized.toLowerCase() : normalized;
}

/// Deletes [directory] with short backoff retries for Windows WebView2 file locks.
///
/// Returns `true` when [directory] no longer exists on disk, or `false` if a
/// native process is still locking files after all retries.
Future<bool> deleteQuizProfileDirectoryWithRetry(
  Directory directory, {
  List<Duration> retryDelays = quizProfileDeleteRetryDelays,
}) async {
  for (var attempt = 0; attempt <= retryDelays.length; attempt++) {
    try {
      if (!await directory.exists()) return true;
      await directory.delete(recursive: true);
      if (!await directory.exists()) return true;
    } on FileSystemException {
      if (attempt == retryDelays.length) return false;
    } catch (_) {
      return !await directory.exists();
    }
    if (attempt < retryDelays.length) {
      await Future<void>.delayed(retryDelays[attempt]);
    }
  }
  return !await directory.exists();
}

/// Best-effort cleanup of stale Windows WebView2 profile folders under [root].
Future<void> sweepQuizProfileDirectories(
  Directory root, {
  Set<String> activePaths = const <String>{},
  List<Duration> retryDelays = quizProfileDeleteRetryDelays,
}) async {
  try {
    if (!await root.exists()) return;
    bool isActive(String candidatePath) {
      final normalizedCandidate = normalizeProfilePath(candidatePath);
      return activePaths.any(
        (activePath) => normalizeProfilePath(activePath) == normalizedCandidate,
      );
    }

    await for (final entity in root.list(followLinks: false)) {
      if (entity is! Directory) continue;
      if (isActive(entity.path)) {
        continue;
      }
      await deleteQuizProfileDirectoryWithRetry(
        entity,
        retryDelays: retryDelays,
      );
    }
  } catch (_) {
    // Cleanup is best-effort and must not disrupt quiz or settings flows.
  }
}
