import 'dart:async';
import 'dart:io';

import 'package:path/path.dart' as path;

const quizProfileDeleteRetryDelays = <Duration>[
  Duration(milliseconds: 100),
  Duration(milliseconds: 250),
  Duration(milliseconds: 500),
];

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
    final normalizedActive = activePaths.map(normalizeProfilePath).toSet();
    await for (final entity in root.list(followLinks: false)) {
      if (entity is! Directory) continue;
      if (normalizedActive.contains(normalizeProfilePath(entity.path))) {
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
