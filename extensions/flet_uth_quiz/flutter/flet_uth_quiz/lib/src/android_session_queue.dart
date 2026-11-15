import 'dart:async';

/// Serializes Android WebView sessions because WebView storage is process-wide.
class AndroidSessionQueue {
  Future<void> _tail = Future<void>.value();
  bool _available = true;

  Future<AndroidSessionLease?> acquire({
    required Future<void> cancelled,
  }) async {
    if (!_available) return null;

    final previous = _tail;
    final release = Completer<void>();
    _tail = previous.then((_) => release.future);

    final canAcquire = await Future.any<bool>([
      previous.then((_) => true),
      cancelled.then((_) => false),
    ]);
    if (!canAcquire || !_available) {
      release.complete();
      return null;
    }
    return AndroidSessionLease._(release);
  }

  /// Prevents another account session if storage cleanup failed or timed out.
  void disable() => _available = false;
}

class AndroidSessionLease {
  AndroidSessionLease._(this._release);

  final Completer<void> _release;

  void release() {
    if (!_release.isCompleted) _release.complete();
  }
}
